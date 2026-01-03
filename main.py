import ttkbootstrap as ttk
from gui import AmbilightController

if __name__ == "__main__":
    # "darkly", "superhero", "solar", "cyborg" are good dark themes
    root = ttk.Window(themename="darkly")
    app = AmbilightController(root)
    root.mainloop()
