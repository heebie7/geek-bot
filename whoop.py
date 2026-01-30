"""
WHOOP API v2 client for geek-bot.
Handles token refresh, recovery, sleep, body measurements.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Tbilisi")
BASE_URL = "https://api.prod.whoop.com/developer"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"

# GitHub storage for tokens (same pattern as bot.py)
WHOOP_TOKENS_FILE = "whoop_tokens.json"


class WhoopClient:
    def __init__(self):
        self.client_id = os.getenv("WHOOP_CLIENT_ID")
        self.client_secret = os.getenv("WHOOP_CLIENT_SECRET")
        self.access_token = os.getenv("WHOOP_ACCESS_TOKEN")
        self.refresh_token = os.getenv("WHOOP_REFRESH_TOKEN")
        self._github_token = os.getenv("GITHUB_TOKEN")
        self._github_repo = os.getenv("GITHUB_REPO", "heebie7/geek-bot")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _refresh_tokens(self) -> bool:
        """Refresh access token using refresh token."""
        if not self.refresh_token:
            logger.error("No refresh token available")
            return False

        try:
            resp = requests.post(TOKEN_URL, data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            resp.raise_for_status()
            tokens = resp.json()

            self.access_token = tokens["access_token"]
            self.refresh_token = tokens.get("refresh_token", self.refresh_token)

            # Save updated tokens to GitHub
            self._save_tokens_to_github(tokens)
            logger.info("WHOOP tokens refreshed")
            return True
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            return False

    def _save_tokens_to_github(self, tokens: dict):
        """Persist tokens to GitHub repo."""
        if not self._github_token:
            return
        try:
            from github import Github
            g = Github(self._github_token)
            repo = g.get_repo(self._github_repo)
            content_str = json.dumps(tokens, indent=2)
            try:
                existing = repo.get_contents(WHOOP_TOKENS_FILE)
                repo.update_file(WHOOP_TOKENS_FILE, "Update WHOOP tokens", content_str, existing.sha)
            except:
                repo.create_file(WHOOP_TOKENS_FILE, "Save WHOOP tokens", content_str)
        except Exception as e:
            logger.error(f"Failed to save tokens to GitHub: {e}")

    def _load_tokens_from_github(self):
        """Load tokens from GitHub if env vars are empty."""
        if self.access_token and self.refresh_token:
            return
        if not self._github_token:
            return
        try:
            from github import Github
            g = Github(self._github_token)
            repo = g.get_repo(self._github_repo)
            content = repo.get_contents(WHOOP_TOKENS_FILE)
            tokens = json.loads(content.decoded_content.decode("utf-8"))
            self.access_token = tokens.get("access_token", self.access_token)
            self.refresh_token = tokens.get("refresh_token", self.refresh_token)
            logger.info("Loaded WHOOP tokens from GitHub")
        except Exception as e:
            logger.debug(f"No stored WHOOP tokens: {e}")

    def _api_get(self, endpoint: str, params: dict = None) -> dict | None:
        """Make authenticated GET request with auto-refresh."""
        self._load_tokens_from_github()

        if not self.access_token:
            logger.error("No WHOOP access token")
            return None

        url = f"{BASE_URL}{endpoint}"
        resp = requests.get(url, headers=self._headers(), params=params)

        if resp.status_code == 401:
            # Token expired, try refresh
            if self._refresh_tokens():
                resp = requests.get(url, headers=self._headers(), params=params)
            else:
                return None

        if resp.status_code != 200:
            logger.error(f"WHOOP API error {resp.status_code}: {resp.text}")
            return None

        return resp.json()

    # === Public API methods ===

    def get_recovery_today(self) -> dict | None:
        """Get today's recovery data."""
        now = datetime.now(TZ)
        start = now.replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/recovery", params={
            "start": start,
            "limit": 1,
        })
        if data and data.get("records"):
            return data["records"][0]
        return None

    def get_recovery_week(self) -> list:
        """Get last 7 days of recovery."""
        now = datetime.now(TZ)
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/recovery", params={
            "start": start,
            "limit": 7,
        })
        if data and data.get("records"):
            return data["records"]
        return []

    def get_sleep_today(self) -> dict | None:
        """Get last night's sleep."""
        now = datetime.now(TZ)
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/activity/sleep", params={
            "start": start,
            "limit": 1,
        })
        if data and data.get("records"):
            return data["records"][0]
        return None

    def get_cycle_today(self) -> dict | None:
        """Get today's cycle (day strain)."""
        now = datetime.now(TZ)
        start = now.replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/cycle", params={
            "start": start,
            "limit": 1,
        })
        if data and data.get("records"):
            return data["records"][0]
        return None

    def get_cycles_week(self) -> list:
        """Get last 7 days of cycles (strain data)."""
        now = datetime.now(TZ)
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/cycle", params={
            "start": start,
            "limit": 7,
        })
        if data and data.get("records"):
            return data["records"]
        return []

    def get_body_measurement(self) -> dict | None:
        """Get latest body measurement."""
        data = self._api_get("/v2/user/measurement/body")
        if data:
            return data
        return None

    def get_profile(self) -> dict | None:
        """Get user profile."""
        return self._api_get("/v2/user/profile/basic")

    # === Formatted output ===

    def format_recovery_today(self) -> str:
        """Human-readable recovery summary."""
        rec = self.get_recovery_today()
        if not rec:
            return "WHOOP: нет данных recovery за сегодня."

        score = rec.get("score", {})
        recovery_score = score.get("recovery_score")
        rhr = score.get("resting_heart_rate")
        hrv = score.get("hrv_rmssd_milli")

        parts = ["WHOOP Recovery"]
        if recovery_score is not None:
            # Color indicator
            if recovery_score >= 67:
                indicator = "green"
            elif recovery_score >= 34:
                indicator = "yellow"
            else:
                indicator = "red"
            parts.append(f"Recovery: {recovery_score}% ({indicator})")
        if rhr is not None:
            parts.append(f"RHR: {rhr} bpm")
        if hrv is not None:
            hrv_ms = round(hrv, 1)
            parts.append(f"HRV: {hrv_ms} ms")

        return "\n".join(parts)

    def format_sleep_today(self) -> str:
        """Human-readable sleep summary."""
        sleep = self.get_sleep_today()
        if not sleep:
            return "Нет данных сна."

        score = sleep.get("score", {})
        stage = score.get("stage_summary", {})

        total_ms = stage.get("total_in_bed_time_milli", 0)
        total_hours = round(total_ms / 3_600_000, 1)

        rem_ms = stage.get("total_rem_sleep_time_milli", 0)
        deep_ms = stage.get("total_slow_wave_sleep_time_milli", 0)
        rem_min = round(rem_ms / 60_000)
        deep_min = round(deep_ms / 60_000)

        perf = score.get("sleep_performance_percentage")
        efficiency = score.get("sleep_efficiency_percentage")

        parts = [f"Sleep: {total_hours}h"]
        if perf is not None:
            parts.append(f"Performance: {perf}%")
        if efficiency is not None:
            parts.append(f"Efficiency: {efficiency}%")
        parts.append(f"REM: {rem_min} min, Deep: {deep_min} min")

        return "\n".join(parts)

    def format_weekly_summary(self) -> str:
        """Weekly recovery trend."""
        records = self.get_recovery_week()
        if not records:
            return "WHOOP: нет данных за неделю."

        scores = []
        hrvs = []
        rhrs = []

        for rec in records:
            s = rec.get("score", {})
            if s.get("recovery_score") is not None:
                scores.append(s["recovery_score"])
            if s.get("hrv_rmssd_milli") is not None:
                hrvs.append(s["hrv_rmssd_milli"])
            if s.get("resting_heart_rate") is not None:
                rhrs.append(s["resting_heart_rate"])

        parts = ["WHOOP — неделя"]

        if scores:
            avg_recovery = round(sum(scores) / len(scores))
            green_count = sum(1 for s in scores if s >= 67)
            yellow_count = sum(1 for s in scores if 34 <= s < 67)
            red_count = sum(1 for s in scores if s < 34)
            parts.append(f"Recovery avg: {avg_recovery}%")
            parts.append(f"  green: {green_count}, yellow: {yellow_count}, red: {red_count}")

        if hrvs:
            avg_hrv = round(sum(hrvs) / len(hrvs), 1)
            parts.append(f"HRV avg: {avg_hrv} ms")

        if rhrs:
            avg_rhr = round(sum(rhrs) / len(rhrs))
            parts.append(f"RHR avg: {avg_rhr} bpm")

        # Body measurement
        body = self.get_body_measurement()
        if body:
            bm = body.get("weight_kilogram") or body.get("body_mass_kg")
            bf = body.get("body_fat_percentage")
            if bm:
                parts.append(f"Weight: {round(bm, 1)} kg")
            if bf:
                parts.append(f"Body fat: {round(bf, 1)}%")

        return "\n".join(parts)


# Singleton
whoop_client = WhoopClient()
