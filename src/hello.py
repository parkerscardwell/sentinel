"""M0 pipe test: prove scheduling + secrets + Slack delivery before any logic."""
from datetime import datetime, timezone
from .slack_client import dm_parker

if __name__ == "__main__":
    dm_parker(f":satellite: Linear Sentinel pipe test — "
              f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print("hello DM sent")
