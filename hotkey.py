import sys
import threading
from typing import Callable, Optional

class GlobalShortcutListener(threading.Thread):
    """
    Listens for the Special Shortcut (Ctrl + Shift + X) globally on Windows.
    Runs quietly in the background without stealing focus or interrupting work
    until the shortcut is explicitly pressed.
    """
    def __init__(self, on_trigger: Callable):
        super().__init__(daemon=True)
        self.on_trigger = on_trigger
        self.running = True
        self._thread_id = None

    def run(self):
        if sys.platform != "win32":
            return

        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        self._thread_id = kernel32.GetCurrentThreadId()

        # MOD_CONTROL (0x0002) | MOD_SHIFT (0x0004) | MOD_NOREPEAT (0x4000)
        MODIFIERS = 0x0002 | 0x0004 | 0x4000
        HOTKEY_ID = 101
        VK_X = 0x58  # Virtual key 'X'

        # Register Ctrl + Shift + X
        success = user32.RegisterHotKey(None, HOTKEY_ID, MODIFIERS, VK_X)
        if not success:
            print("[Hotkey Notice] Could not register global Ctrl+Shift+X hotkey (already in use). Using in-app shortcut.")
            return

        print("[*] Special Shortcut Registered: Press 'Ctrl + Shift + X' anytime to triage captures.")

        msg = wintypes.MSG()
        try:
            while self.running:
                # Wait for Windows message
                res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if res == 0 or res == -1:
                    break

                if msg.message == 0x0312 and msg.wParam == HOTKEY_ID:  # WM_HOTKEY
                    try:
                        self.on_trigger()
                    except Exception as e:
                        print(f"[Hotkey Trigger Error] {e}")

                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self):
        self.running = False
        if sys.platform == "win32" and self._thread_id:
            import ctypes
            # Post WM_QUIT to unblock GetMessageW
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
