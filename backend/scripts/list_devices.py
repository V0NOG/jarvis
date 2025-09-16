# backend/scripts/list_devices.py
import sounddevice as sd
for i, dev in enumerate(sd.query_devices()):
    print(f"{i:2d} | in={dev['max_input_channels']} out={dev['max_output_channels']} | {dev['name']}")