from __future__ import annotations

import soundcard as sc


def main() -> None:
    print("Default speaker:")
    print(f"  {sc.default_speaker()}")
    print()
    print("Default microphone:")
    print(f"  {sc.default_microphone()}")
    print()
    print("All speakers:")
    for index, speaker in enumerate(sc.all_speakers()):
        print(f"  [{index}] {speaker}")
    print()
    print("All microphones:")
    for index, mic in enumerate(sc.all_microphones(include_loopback=True)):
        print(f"  [{index}] {mic}")


if __name__ == "__main__":
    main()
