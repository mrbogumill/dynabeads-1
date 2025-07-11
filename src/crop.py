import os
import sys
import cv2
import argparse
import numpy as np
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import filedialog, simpledialog, ttk
from concurrent.futures import ProcessPoolExecutor, as_completed


# Define a function to get sorted list of video files
def get_sorted_video_files(input):
    video_files = [f for f in os.listdir(input) if f.endswith((".mp4", ".avi"))]
    video_files.sort()
    return [os.path.join(input, f) for f in video_files]


# Extract the first frame from a video
def get_first_frame(video_path, target_width=960, target_height=540):
    cap = cv2.VideoCapture(video_path)
    success, frame = cap.read()
    cap.release()
    if success:
        # Convert to RGB for PIL
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Get original frame size
        orig_height, orig_width = frame.shape[:2]

        # Calculate the scaling factor to fit the frame within the target size, maintaining aspect ratio
        scale_w = target_width / orig_width
        scale_h = target_height / orig_height
        scale = min(scale_w, scale_h)

        # Calculate the new size
        new_width = int(orig_width * scale)
        new_height = int(orig_height * scale)

        # Resize the frame
        resized_frame = cv2.resize(
            frame, (new_width, new_height), interpolation=cv2.INTER_AREA
        )

        return resized_frame

    return None


# Assuming you have a method to get the frame size
def get_frame_size(video_path):
    cap = cv2.VideoCapture(video_path)
    if cap.isOpened():
        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        cap.release()
        return int(width), int(height)
    return None, None


def detect_beads(frame):
    """Detect the centers of beads in a frame."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    centers = []
    for cnt in contours:
        M = cv2.moments(cnt)
        if M["m00"] != 0:
            centers.append((int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])))
    return centers


def export_selected_beads(input, output, video_path, selections, crop_size):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"{video_path} does not exist.")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise cv2.error("Error opening video capture.", "export_selected_beads")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")

    os.makedirs(output, exist_ok=True)

    writers = []
    half_crop_size = crop_size // 2
    for i, selected in enumerate(selections):
        i += 1
        if selected:
            cx, cy = selected
            out_dir = os.path.join(output, f"bead_{i}")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, os.path.basename(video_path))
            writers.append(
                (cv2.VideoWriter(out_path, fourcc, fps, (crop_size, crop_size)), (cx, cy))
            )

    for i in range(total_frames):
        ret, frame = cap.read()
        if not ret:
            break
        for writer, (cx, cy) in writers:
            y1 = max(0, cy - half_crop_size)
            y2 = min(cy + half_crop_size, frame.shape[0])
            x1 = max(0, cx - half_crop_size)
            x2 = min(cx + half_crop_size, frame.shape[1])
            
            # Extract the crop region
            crop_region = frame[y1:y2, x1:x2]
            
            # If the crop region is smaller than expected (due to frame boundaries),
            # pad it to maintain the expected size
            if crop_region.shape[0] != crop_size or crop_region.shape[1] != crop_size:
                padded_crop = np.zeros((crop_size, crop_size, 3), dtype=np.uint8)
                h, w = crop_region.shape[:2]
                padded_crop[:h, :w] = crop_region
                crop_region = padded_crop
            
            writer.write(crop_region)

    cap.release()
    for writer, _ in writers:
        writer.release()


class VideoFrameExplorer:
    def __init__(self, child, input, output, progress=None):
        video_paths = get_sorted_video_files(input)
        self.root = child
        self.progress = progress
        self.video_paths = video_paths
        self.input = input
        self.output = output
        self.current_index = 0
        self.detected_centers = []
        self.crop_size = 100  # Default crop size (can be changed)

        self.canvas = tk.Canvas(self.root, width=960, height=540)
        self.canvas.pack()

        # Frame for crop size controls
        self.crop_size_frame = ttk.Frame(self.root)
        self.crop_size_frame.pack(side=tk.BOTTOM, pady=(5, 0))
        
        ttk.Label(self.crop_size_frame, text="Crop Size:").pack(side=tk.LEFT)
        self.crop_size_var = tk.StringVar(value=str(self.crop_size))
        self.crop_size_entry = ttk.Entry(self.crop_size_frame, textvariable=self.crop_size_var, width=6)
        self.crop_size_entry.pack(side=tk.LEFT, padx=(5, 5))
        self.crop_size_entry.bind('<Return>', self.update_crop_size)
        self.crop_size_entry.bind('<FocusOut>', self.update_crop_size)
        
        self.update_size_button = ttk.Button(
            self.crop_size_frame, text="Update Size", command=self.update_crop_size
        )
        self.update_size_button.pack(side=tk.LEFT)

        # Frame for export buttons, placed correctly below the navigation frame
        self.export_button_frame = ttk.Frame(self.root)
        self.export_button_frame.pack(side=tk.BOTTOM, pady=(5, 10))

        self.export_all_button = ttk.Button(
            self.export_button_frame, text=f"Export up to Video {self.current_index+1}", command=self.export
        )
        self.export_all_button.pack(side=tk.LEFT)
        self.export_single_button = ttk.Button(
            self.export_button_frame,
            text=f"Export Video {self.current_index+1}",
            command=self.export_one,
        )
        self.export_single_button.pack(side=tk.LEFT)

        self.edit2_button_frame = ttk.Frame(self.root)
        self.edit2_button_frame.pack(side=tk.BOTTOM, pady=(5, 0))

        # Remove all rectangles button
        self.remove_rects_button = ttk.Button(
            self.edit2_button_frame, text="Remove All Rectangles", command=self.remove_all_rectangles
        )
        self.remove_rects_button.pack(side=tk.LEFT)

        # Recreate rectangles button
        self.recreate_rects_button = ttk.Button(
            self.edit2_button_frame, text="Recreate Rectangles", command=self.recreate_rectangles
        )
        self.recreate_rects_button.pack(side=tk.LEFT)

        self.edit_button_frame = ttk.Frame(self.root)
        self.edit_button_frame.pack(side=tk.BOTTOM, pady=(5, 0))

        self.new_x = ttk.Entry(self.edit_button_frame, width=5)
        self.new_x.pack(side=tk.LEFT, padx=(2, 0))
        self.new_x.insert(0, "x")

        # Entry for Y coordinate
        self.new_y = ttk.Entry(self.edit_button_frame, width=5)
        self.new_y.pack(side=tk.LEFT, padx=(2, 0))
        self.new_y.insert(0, "y")

        self.custom_rect_button = ttk.Button(
            self.edit_button_frame, text="Add Custom Rectangle", command=self.add_custom_rectangle
        )
        self.custom_rect_button.pack(side=tk.LEFT, padx=(0, 20))

        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Button-2>", self.on_canvas_right_click)
        # Different systems have different default bindings, so checking both here
        self.canvas.bind("<Button-3>", self.on_canvas_right_click)

        self.nav_button_frame = ttk.Frame(self.root)
        self.nav_button_frame.pack(side=tk.BOTTOM, pady=(5, 0))

        # Navigation buttons
        self.first_button = ttk.Button(
            self.nav_button_frame, text="<< First", command=self.show_first_frame
        )
        self.first_button.pack(side=tk.LEFT)

        self.prev_button = ttk.Button(
            self.nav_button_frame, text="< Prev", command=self.show_prev_frame
        )
        self.prev_button.pack(side=tk.LEFT)

        self.next_button = ttk.Button(
            self.nav_button_frame, text="Next >", command=self.show_next_frame
        )
        self.next_button.pack(side=tk.LEFT)

        self.last_button = ttk.Button(
            self.nav_button_frame, text="Last >>", command=self.show_last_frame
        )
        self.last_button.pack(side=tk.LEFT)

        frame = get_first_frame(self.video_paths[self.current_index])
        self.detect_and_draw_centers(frame)
        self.show_frame(self.current_index)
        self.update_button_states()

        self.root.resizable(False, False)

        try:
            self.root.eval(f"tk::PlaceWindow . center")
        except:
            pass

        self.root.deiconify()

    def update_crop_size(self, event=None):
        """Update the crop size and redraw rectangles"""
        try:
            new_size = int(self.crop_size_var.get())
            if new_size > 0:
                self.crop_size = new_size
                self.redraw_rectangles()
        except ValueError:
            # Reset to current value if invalid input
            self.crop_size_var.set(str(self.crop_size))

    def update_button_states(self):
        # Disable "First" and "Prev" buttons if on the first video
        if self.current_index == 0:
            self.first_button["state"] = "disabled"
            self.prev_button["state"] = "disabled"
        else:
            self.first_button["state"] = "normal"
            self.prev_button["state"] = "normal"

        # Disable "Next" and "Last" buttons if on the last video
        if self.current_index >= len(self.video_paths) - 1:
            self.next_button["state"] = "disabled"
            self.last_button["state"] = "disabled"
        else:
            self.next_button["state"] = "normal"
            self.last_button["state"] = "normal"

    def update_canvas(self, img):
        self.canvas.delete("all")
        self.photo = ImageTk.PhotoImage(image=Image.fromarray(img))
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
        self.redraw_rectangles()

    def add_custom_rectangle(self):
        try:
            if self.new_x.get() is not None and int(self.new_x.get()) > 0 and self.new_y.get() is not None and int(self.new_y.get()) > 0:
                self.detected_centers.append(((int(self.new_x.get()) * self.get_scale(), int(self.new_y.get()) * self.get_scale()), True))
                self.redraw_rectangles()
        except:
            pass

    def detect_and_draw_centers(self, frame):
        centers = detect_beads(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))
        self.detected_centers = self.detected_centers + [(center, False) for center in centers]

    def redraw_rectangles(self):
        # Clear existing rectangles
        self.canvas.delete("rect")
        for center, clicked in self.detected_centers:
            self.draw_rectangle(center, clicked)

    def remove_all_rectangles(self):
        self.detected_centers.clear()
        frame = get_first_frame(self.video_paths[self.current_index])
        if frame is not None:
            self.update_canvas(frame)
        self.redraw_rectangles()

    def recreate_rectangles(self):
        frame = get_first_frame(self.video_paths[self.current_index])
        if frame is not None:
            self.detect_and_draw_centers(frame)
            self.update_canvas(frame)

    def draw_rectangle(self, center, clicked):
        scale = self.get_scale()

        # Scale the rectangle size according to the current crop_size and scaling factor
        half_side_length_scaled = (self.crop_size // 2) * scale

        x, y = center
        # Apply scaled half_side_length for rectangle coordinates
        x1, y1 = x - half_side_length_scaled, y - half_side_length_scaled
        x2, y2 = x + half_side_length_scaled, y + half_side_length_scaled
        outline_color = "light green" if clicked else "red"
        self.canvas.create_rectangle(x1, y1, x2, y2, outline=outline_color, tags="rect")
        # Create text label with x and y coordinates and crop size
        self.canvas.create_text(
            (x1-(x2-x1)/1.5), y1-15, text=f"({int(x / scale)}, {int(y / scale)}) [{self.crop_size}x{self.crop_size}]", 
            anchor=tk.NW, tags="rect"
        )

    def get_scale(self):
        orig_width, orig_height = get_frame_size(self.video_paths[self.current_index])
        disp_width, disp_height = 960, 540

        # Calculate scaling factors for width and height
        scale_w = disp_width / orig_width if orig_width else 1
        scale_h = disp_height / orig_height if orig_height else 1
        return min(scale_w, scale_h)

    def on_canvas_click(self, event):
        x, y = event.x, event.y
        orig_width, orig_height = get_frame_size(self.video_paths[self.current_index])
        disp_width = 960
        scale = disp_width / orig_width if orig_width else 1
        half_side_length_scaled = scale * (self.crop_size // 2)
        for i, (center, clicked) in enumerate(self.detected_centers):
            x1, y1 = (
                center[0] - half_side_length_scaled,
                center[1] - half_side_length_scaled,
            )
            x2, y2 = (
                center[0] + half_side_length_scaled,
                center[1] + half_side_length_scaled,
            )
            if x1 <= x <= x2 and y1 <= y <= y2:
                # Toggle the clicked state and redraw the canvas
                self.detected_centers[i] = (center, not clicked)
                self.redraw_rectangles()
                break

    def on_canvas_right_click(self, event):
        x, y = event.x, event.y
        orig_width, orig_height = get_frame_size(self.video_paths[self.current_index])
        disp_width = 960
        scale = disp_width / orig_width if orig_width else 1
        half_side_length_scaled = scale * (self.crop_size // 2)
        for i, (center, clicked) in enumerate(self.detected_centers):
            x1, y1 = (
                center[0] - half_side_length_scaled,
                center[1] - half_side_length_scaled,
            )
            x2, y2 = (
                center[0] + half_side_length_scaled,
                center[1] + half_side_length_scaled,
            )
            if x1 <= x <= x2 and y1 <= y <= y2:
                del self.detected_centers[i]
                self.show_frame(
                    self.current_index
                )  # More emphatic redraw, the normal redraw doesn't remove already existing ones
                break

    def show_frame(self, index):
        self.current_index = index
        frame = get_first_frame(self.video_paths[index])
        if frame is not None:
            self.update_canvas(frame)
            self.export_single_button.config(
                text=f"Export Video {self.current_index+1}"
            )
            self.export_all_button.config(
                text=f"Export up to Video {self.current_index+1}"
            )
            self.redraw_rectangles()
        self.update_button_states()

    def export(self):
        orig_width, orig_height = get_frame_size(self.video_paths[self.current_index])
        disp_width = 960
        scale = disp_width / orig_width if orig_width else 1
        selected_centers = [
            (int(center[0] / scale), int(center[1] / scale))
            for center, clicked in self.detected_centers
            if clicked
        ]
        self.progress.reset(total=self.current_index + 1)

        if selected_centers:
            try:
                self.progress._tk_window.deiconify()
            except:
                pass
            with ProcessPoolExecutor() as executor:
                # Map futures to video file names
                future_to_video = {
                    executor.submit(
                        export_selected_beads,
                        self.input,
                        self.output,
                        video_file,
                        selected_centers,
                        self.crop_size,  # Pass the current crop size
                    ): video_file
                    for video_file in self.video_paths[0 : self.current_index + 1]
                }

                for future in as_completed(future_to_video):
                    # Update the progress bar manually
                    self.progress.update(1)

                    try:
                        result = future.result()
                    except Exception as exc:
                        video_name = future_to_video[future]
                        print(f"{video_name} generated an exception: {exc}")

                # Finish the progress bar
                self.progress.n = self.progress.total
                self.progress.refresh()
                try:
                    self.progress._tk_window.withdraw()
                except:
                    pass
        else:
            print("No centers selected for this video.")

    def export_one(self):
        orig_width, orig_height = get_frame_size(self.video_paths[self.current_index])
        disp_width = 960
        scale = disp_width / orig_width if orig_width else 1
        selected_centers = [
            (int(center[0] / scale), int(center[1] / scale))
            for center, clicked in self.detected_centers
            if clicked
        ]

        if selected_centers:
            export_selected_beads(self.input,
            self.output,
            self.video_paths[self.current_index],
            selected_centers,
            self.crop_size)  # Pass the current crop size
        else:
            print("No centers selected for this video.")

    def show_first_frame(self):
        self.current_index = 0
        self.show_frame(self.current_index)

    def show_prev_frame(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.show_frame(self.current_index)

    def show_next_frame(self):
        if self.current_index < len(self.video_paths) - 1:
            self.current_index += 1
            self.show_frame(self.current_index)

    def show_last_frame(self):
        self.current_index = len(self.video_paths) - 1
        self.show_frame(self.current_index)


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    root.title("Crop Tool")
    parser = argparse.ArgumentParser(
        description="Crop videos down to individual beads for tracking."
    )
    # Integration with the file dialog to choose the folder
    input = "raw"
    output = "exported_beads"
    # input = filedialog.askdirectory(initialdir=".", title="Select Input Folder")
    if not input:
        tk.messagebox.showerror("Error", "You must select an input folder")
        sys.exit(0)
    # output = filedialog.askdirectory(initialdir=".", title="Select Output Folder")
    if not output:
        tk.messagebox.showerror("Error", "You must select an output folder")
        sys.exit(0)
    explorer = VideoFrameExplorer(root, input, output)

    root.mainloop()
