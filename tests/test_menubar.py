"""
Baby step 7: Menu bar UI stub.
Cycles through the four states so we can verify the icon and menu look right.
"""
import rumps
import threading
import time

class ClaudeApp(rumps.App):
    def __init__(self):
        super().__init__("⚪")  # start in idle state

        self.menu = [
            rumps.MenuItem("Status: Idle"),
            None,  # separator
            rumps.MenuItem("Test: cycle states", callback=self.cycle_states),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

    def set_state(self, state):
        icons = {
            "idle":      "⚪",
            "listening": "🔴",
            "thinking":  "🟡",
            "speaking":  "🟢",
        }
        labels = {
            "idle":      "Status: Idle",
            "listening": "Status: Listening...",
            "thinking":  "Status: Thinking...",
            "speaking":  "Status: Speaking...",
        }
        self.title = icons[state]
        self.menu["Status: Idle"].title = labels[state]

    def cycle_states(self, _):
        """Cycle through all states with a 1.5s pause so you can see each one."""
        def run():
            for state in ["listening", "thinking", "speaking", "idle"]:
                self.set_state(state)
                time.sleep(1.5)
        threading.Thread(target=run, daemon=True).start()

    def quit_app(self, _):
        rumps.quit_application()

if __name__ == "__main__":
    print("Starting app...")
    app = ClaudeApp()
    print("App created, launching menu bar...")
    app.run()
    print("App exited.")
