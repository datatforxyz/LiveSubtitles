# __main__.py
from rich.console import Console
from .overlay_mac import OverlayApp
from .pipeline import run_pipeline

console = Console()

def main():
    console.print("[green]Starting native macOS overlay for EN→ES subtitles...[/green]")

    overlay = OverlayApp()
    overlay.start()  # spins AppKit runloop on a background thread

    try:
        run_pipeline(overlay.set_text)
    except KeyboardInterrupt:
        console.print("\n[cyan]Stopping...[/cyan]")
    finally:
        overlay.stop()

if __name__ == "__main__":
    main()
