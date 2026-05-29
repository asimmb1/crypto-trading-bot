"""
confirm.py — Reset the dead man's switch timer.

Run this every day to keep the bots alive:
    python -m src.confirm

Also used to resume after a dead man's switch halt.
"""

from src.dead_mans_switch import DeadMansSwitch


def main():
    dms = DeadMansSwitch()
    status_before = dms.status()
    dms.confirm()
    print(f"\n✅ Heartbeat confirmed.")
    print(f"   Previous confirmation: {status_before['last_confirmed']}")
    print(f"   Gap since last confirm: {status_before['hours_since']:.1f}h")
    print(f"   Next required within: 30h\n")


if __name__ == "__main__":
    main()
