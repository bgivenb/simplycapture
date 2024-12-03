import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import cv2
import numpy as np
import threading
import keyboard
import time
import datetime
import os
import mss
from PIL import Image, ImageTk


def resource_path(relative_path):
    """ this is the absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)



class ScreenRecorder:
    def __init__(self, root):
        self.root = root
        self.recording = False
        self.region = None
        self.save_folder = os.getcwd()  # Default to current directory
        self.recording_thread = None
        self.fps = 20.0  # Frames per second

        # Initialize images
        self.load_images()

        # Initialize GUI
        self.init_gui()

        # Register hotkey to stop recording
        keyboard.add_hotkey('ctrl+shift+s', self.stop_recording)

    def load_images(self):
        try:
            # Load and resize the start icon
            self.start_icon = Image.open(resource_path("assets/icon.png"))
            self.start_icon = self.start_icon.resize((128, 128), Image.Resampling.LANCZOS)
            self.start_icon_tk = ImageTk.PhotoImage(self.start_icon)

            # Load and resize the hover icon
            self.hover_icon = Image.open(resource_path("assets/icon_hover.png"))
            self.hover_icon = self.hover_icon.resize((128, 128), Image.Resampling.LANCZOS)
            self.hover_icon_tk = ImageTk.PhotoImage(self.hover_icon)

            # Load and resize the stop icon (using the same icon.png for simplicity)
            self.stop_icon = Image.open(resource_path("assets/icon.png"))
            self.stop_icon = self.stop_icon.resize((128, 128), Image.Resampling.LANCZOS)
            self.stop_icon_tk = ImageTk.PhotoImage(self.stop_icon)

        except Exception as e:
            messagebox.showerror("Image Loading Error", f"Error loading images: {e}")
            self.root.destroy()

    def init_gui(self):
        # Set window title and icon
        self.root.title("Screen Recorder")
        try:
            self.root.iconphoto(False, self.start_icon_tk)
        except Exception as e:
            print(f"Error setting window icon: {e}")

        # Set window size and background color
        self.root.geometry("500x400")
        self.root.configure(bg="#313338")

        # Set up ttk style
        self.style = ttk.Style()
        self.set_theme("dark")

        # Browse Save Folder Button
        self.browse_button = ttk.Button(self.root, text="Browse Save Folder", command=self.browse_folder)
        self.browse_button.pack(pady=10)

        # Display selected save folder
        self.folder_label = ttk.Label(self.root, text=f"Save Folder: {self.save_folder}", wraplength=480, justify="left", foreground="#949ba4", background="#313338")
        self.folder_label.pack(pady=5)

        # Select Recording Region Button
        self.select_region_button = ttk.Button(self.root, text="Select Recording Region", command=self.select_region)
        self.select_region_button.pack(pady=10)

        # Start/Stop Recording Button (Icon Button)
        self.record_button = ttk.Label(self.root, image=self.start_icon_tk, cursor="hand2")
        self.record_button.pack(pady=20)
        self.record_button.bind("<Button-1>", self.toggle_recording)
        self.record_button.bind("<Enter>", self.on_hover)
        self.record_button.bind("<Leave>", self.on_leave)

        # Status Label
        self.status_label = ttk.Label(self.root, text="Status: Idle", foreground="#949ba4", background="#313338")
        self.status_label.pack(pady=10)

        # Apply additional style configurations
        self.style.configure("TButton",
                             background="#383a40",
                             foreground="#949ba4",
                             borderwidth=0,
                             focusthickness=3,
                             focuscolor="#726d95")
        self.style.map("TButton",
                       background=[('active', '#726d95')],
                       foreground=[('active', '#ffffff')])

    def set_theme(self, mode):
        if mode == "dark":
            self.style.theme_use("clam")

            # Configure colors
            self.style.configure('.', 
                                 background="#313338",
                                 foreground="#949ba4",
                                 font=("Segoe UI", 10),
                                 bordercolor="#383a40")
            self.style.configure('TButton', 
                                 background="#383a40",
                                 foreground="#949ba4",
                                 borderwidth=1,
                                 focusthickness=3,
                                 focuscolor="#726d95")

            self.style.map('TButton',
                           background=[('active', '#726d95')],
                           foreground=[('active', '#ffffff')])

            # Configure labels
            self.style.configure('TLabel',
                                 background="#313338",
                                 foreground="#949ba4")

    def browse_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.save_folder = folder_selected
            self.folder_label.config(text=f"Save Folder: {self.save_folder}")

    def select_region(self):
        self.root.withdraw()  # Hide main window during selection
        selection_window = tk.Toplevel()
        selection_window.attributes("-fullscreen", True)
        selection_window.attributes("-alpha", 0.3)
        selection_window.configure(background='black')
        selection_window.bind("<ButtonPress-1>", self.on_mouse_down)
        selection_window.bind("<B1-Motion>", self.on_mouse_move)
        selection_window.bind("<ButtonRelease-1>", self.on_mouse_up)

        self.start_x = self.start_y = self.end_x = self.end_y = 0
        self.rect = None
        self.selection_canvas = tk.Canvas(selection_window, cursor="cross", bg="black")
        self.selection_canvas.pack(fill=tk.BOTH, expand=True)

        self.selection_canvas.bind("<Escape>", lambda e: self.cancel_selection(selection_window))

        self.selection_window = selection_window
        self.selection_canvas.focus_set()
        selection_window.mainloop()

    def on_mouse_down(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.selection_canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline='red', width=2)

    def on_mouse_move(self, event):
        current_x, current_y = event.x, event.y
        self.selection_canvas.coords(self.rect, self.start_x, self.start_y, current_x, current_y)

    def on_mouse_up(self, event):
        self.end_x = event.x
        self.end_y = event.y
        self.selection_window.destroy()
        self.root.deiconify()

        # Calculate region
        left = min(self.start_x, self.end_x)
        top = min(self.start_y, self.end_y)
        width = abs(self.end_x - self.start_x)
        height = abs(self.end_y - self.start_y)

        self.region = {
            "top": top,
            "left": left,
            "width": width,
            "height": height
        }
        self.status_label.config(text=f"Selected Region: {self.region}")
        self.record_button.config(state="normal")
        messagebox.showinfo("Region Selected", f"Recording region set to: {self.region}")

    def cancel_selection(self, window):
        window.destroy()
        self.root.deiconify()
        messagebox.showinfo("Selection Cancelled", "Region selection was cancelled.")

    def toggle_recording(self, event=None):
        if not self.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        if not self.region:
            messagebox.showerror("Error", "Please select a region before starting the recording.")
            return

        self.recording = True
        self.status_label.config(text="Status: Recording", foreground="#726d95")
        self.update_record_button()

        self.recording_thread = threading.Thread(target=self.record_screen)
        self.recording_thread.start()

    def stop_recording(self):
        if self.recording:
            self.recording = False
            self.status_label.config(text="Status: Stopped", foreground="#949ba4")
            self.update_record_button()

    def update_record_button(self):
        if self.recording:
            self.record_button.config(image=self.stop_icon_tk)
        else:
            self.record_button.config(image=self.start_icon_tk)

    def on_hover(self, event):
        if not self.recording:
            self.record_button.config(image=self.hover_icon_tk)

    def on_leave(self, event):
        if not self.recording:
            self.record_button.config(image=self.start_icon_tk)

    def record_screen(self):
        # Generate filename with current date and time
        timestamp = datetime.datetime.now().strftime("%m%d%y_%H%M%S")
        filename = f"screenrecording_{timestamp}.avi"
        filepath = os.path.join(self.save_folder, filename)

        # Define the codec and create VideoWriter object
        codec = cv2.VideoWriter_fourcc(*"XVID")
        out = cv2.VideoWriter(filepath, codec, self.fps, (self.region["width"], self.region["height"]))

        with mss.mss() as sct:
            try:
                last_time = time.time()
                while self.recording:
                    # Capture the screen
                    img = sct.grab(self.region)
                    # Convert to a format suitable for OpenCV
                    frame = np.array(img)
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    # Write the frame
                    out.write(frame)

                    # Maintain the desired FPS
                    elapsed = time.time() - last_time
                    time_to_sleep = max(0, (1 / self.fps) - elapsed)
                    time.sleep(time_to_sleep)
                    last_time = time.time()
            except Exception as e:
                messagebox.showerror("Recording Error", f"An error occurred during recording: {e}")
            finally:
                out.release()
                cv2.destroyAllWindows()
                if not self.recording:
                    self.status_label.config(text=f"Recording saved as {filename}", foreground="#949ba4")
                else:
                    self.status_label.config(text="Recording stopped unexpectedly.", foreground="#726d95")

    def on_closing(self):
        if self.recording:
            if messagebox.askokcancel("Quit", "Recording is in progress. Do you want to quit?"):
                self.recording = False
                self.root.destroy()
        else:
            self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = ScreenRecorder(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
