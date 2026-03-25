from setuptools import setup

APP = ["app.py"]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["rumps", "openai", "elevenlabs", "faster_whisper", "sounddevice", "soundfile", "numpy", "dotenv", "pipeline"],
    "resources": ["audio_capture", "assets"],
    "plist": {
        "LSUIElement": True,  # hide Dock icon — menu bar only
        "CFBundleName": "Operator",
        "CFBundleDisplayName": "Operator",
        "CFBundleIdentifier": "com.operator.meeting-participant.v2",
        "NSScreenCaptureUsageDescription": "Operator needs screen recording access to capture meeting audio.",
    },
}

setup(
    name="Operator",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
