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
        self._tokens_loaded_at = None

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
        """Load latest tokens from GitHub (cached for 30 min)."""
        if not self._github_token:
            return
        # Only reload every 30 minutes to avoid excessive GitHub API calls
        now = datetime.now(TZ)
        if self._tokens_loaded_at and (now - self._tokens_loaded_at) < timedelta(minutes=30):
            return
        try:
            from github import Github
            g = Github(self._github_token)
            repo = g.get_repo(self._github_repo)
            content = repo.get_contents(WHOOP_TOKENS_FILE)
            tokens = json.loads(content.decoded_content.decode("utf-8"))
            self.access_token = tokens.get("access_token", self.access_token)
            self.refresh_token = tokens.get("refresh_token", self.refresh_token)
            self._tokens_loaded_at = now
            logger.info("Loaded WHOOP tokens from GitHub")
        except Exception as e:
            self._tokens_loaded_at = now  # Don't retry immediately on error
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
            # Token expired — force reload from GitHub, then retry
            self._tokens_loaded_at = None
            self._load_tokens_from_github()
            resp = requests.get(url, headers=self._headers(), params=params)

            if resp.status_code == 401:
                # Still expired — do a full refresh
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

    def get_sleep_week(self) -> list:
        """Get last 7 days of sleep."""
        now = datetime.now(TZ)
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/activity/sleep", params={
            "start": start,
            "limit": 7,
        })
        if data and data.get("records"):
            return data["records"]
        return []

    def get_cycle_yesterday(self) -> dict | None:
        """Get yesterday's cycle (strain). Use this for morning reports instead of today."""
        now = datetime.now(TZ)
        yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()
        yesterday_end = now.replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/cycle", params={
            "start": yesterday_start,
            "end": yesterday_end,
            "limit": 1,
        })
        if data and data.get("records"):
            return data["records"][0]
        return None

    def get_recovery_3_days(self) -> list:
        """Get last 3 days of recovery for trend analysis."""
        now = datetime.now(TZ)
        start = (now - timedelta(days=3)).replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/recovery", params={
            "start": start,
            "limit": 4,  # Today + 3 previous days
        })
        if data and data.get("records"):
            return data["records"]
        return []

    def get_trend_3_days(self) -> dict:
        """Analyze 3-day recovery trend.

        Returns:
            {
                "direction": "up" | "down" | "stable",
                "scores": [oldest, ..., newest],
                "prev_avg": average of previous 2 days,
                "current": today's recovery
            }
        """
        records = self.get_recovery_3_days()
        if len(records) < 2:
            return {"direction": "stable", "scores": [], "prev_avg": None, "current": None}

        scores = []
        for rec in records:
            s = rec.get("score", {}).get("recovery_score")
            if s is not None:
                scores.append(s)

        if len(scores) < 2:
            return {"direction": "stable", "scores": scores, "prev_avg": None, "current": None}

        current = scores[-1]  # Most recent (today)
        prev_scores = scores[:-1]  # Previous days
        prev_avg = sum(prev_scores) / len(prev_scores)

        # Determine trend direction
        diff = current - prev_avg
        if diff > 10:
            direction = "up"
        elif diff < -10:
            direction = "down"
        else:
            direction = "stable"

        return {
            "direction": direction,
            "scores": scores,
            "prev_avg": round(prev_avg),
            "current": current
        }

    def get_workouts_today(self) -> list:
        """Get today's workouts."""
        now = datetime.now(TZ)
        start = now.replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/activity/workout", params={
            "start": start,
            "limit": 10,
        })
        if data and data.get("records"):
            return data["records"]
        return []

    def get_workouts_yesterday(self) -> list:
        """Get yesterday's workouts."""
        now = datetime.now(TZ)
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()
        end = now.replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/activity/workout", params={
            "start": start,
            "end": end,
            "limit": 10,
        })
        if data and data.get("records"):
            return data["records"]
        return []

    def get_workouts_week(self) -> list:
        """Get last 7 days of workouts."""
        now = datetime.now(TZ)
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0).isoformat()
        data = self._api_get("/v2/activity/workout", params={
            "start": start,
            "limit": 50,
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
        """Human-readable sleep summary (actual sleep, not in-bed)."""
        sleep = self.get_sleep_today()
        if not sleep:
            return "Нет данных сна."

        score = sleep.get("score", {})
        stage = score.get("stage_summary", {})

        rem_ms = stage.get("total_rem_sleep_time_milli", 0)
        deep_ms = stage.get("total_slow_wave_sleep_time_milli", 0)
        light_ms = stage.get("total_light_sleep_time_milli", 0)
        rem_min = round(rem_ms / 60_000)
        deep_min = round(deep_ms / 60_000)
        light_min = round(light_ms / 60_000)
        actual_min = rem_min + deep_min + light_min
        actual_h = round(actual_min / 60, 1)

        in_bed_ms = stage.get("total_in_bed_time_milli", 0)
        in_bed_h = round(in_bed_ms / 3_600_000, 1)

        perf = score.get("sleep_performance_percentage")
        efficiency = score.get("sleep_efficiency_percentage")

        parts = [f"Sleep: {actual_h}h (in bed {in_bed_h}h)"]
        if perf is not None:
            parts.append(f"Performance: {perf}%")
        if efficiency is not None:
            parts.append(f"Efficiency: {efficiency}%")
        parts.append(f"REM: {rem_min} min, Deep: {deep_min} min, Light: {light_min} min")

        return "\n".join(parts)

    def format_daily_note(self, rec=None, sleep=None, body=None, cycle=None, workouts=None) -> str:
        """Generate daily note with YAML frontmatter for Obsidian.

        All parameters are raw API responses (or None if unavailable).
        Returns full markdown content ready to save as YYYY-MM-DD.md.
        """
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        # === Extract fields ===
        # Recovery
        recovery = None
        recovery_state = None
        rhr = None
        hrv = None
        spo2 = None
        skin_temp = None
        if rec:
            s = rec.get("score", {})
            recovery = s.get("recovery_score")
            rhr = s.get("resting_heart_rate")
            hrv_raw = s.get("hrv_rmssd_milli")
            hrv = round(hrv_raw, 1) if hrv_raw is not None else None
            spo2 = s.get("spo2_percentage")
            skin_temp = s.get("skin_temp_celsius")
            if recovery is not None:
                recovery_state = "green" if recovery >= 67 else ("yellow" if recovery >= 34 else "red")

        # Sleep
        in_bed_hours = None
        actual_sleep_min = None
        sleep_perf = None
        sleep_eff = None
        sleep_consistency = None
        respiratory_rate = None
        rem_min = None
        deep_min = None
        light_min = None
        awake_min = None
        disturbances = None
        sleep_need_base_min = None
        sleep_need_debt_min = None
        sleep_need_strain_min = None
        if sleep:
            ss = sleep.get("score", {})
            stage = ss.get("stage_summary", {})
            total_ms = stage.get("total_in_bed_time_milli", 0)
            in_bed_hours = round(total_ms / 3_600_000, 1) if total_ms else None
            sleep_perf = ss.get("sleep_performance_percentage")
            sleep_eff_raw = ss.get("sleep_efficiency_percentage")
            sleep_eff = round(sleep_eff_raw, 1) if sleep_eff_raw is not None else None
            sleep_consistency = ss.get("sleep_consistency_percentage")
            respiratory_rate = ss.get("respiratory_rate")
            if respiratory_rate is not None:
                respiratory_rate = round(respiratory_rate, 1)
            rem_min = round(stage.get("total_rem_sleep_time_milli", 0) / 60_000) if stage.get("total_rem_sleep_time_milli") else None
            deep_min = round(stage.get("total_slow_wave_sleep_time_milli", 0) / 60_000) if stage.get("total_slow_wave_sleep_time_milli") else None
            light_min = round(stage.get("total_light_sleep_time_milli", 0) / 60_000) if stage.get("total_light_sleep_time_milli") else None
            awake_min = round(stage.get("total_awake_time_milli", 0) / 60_000) if stage.get("total_awake_time_milli") else None
            disturbances = stage.get("disturbance_count")
            # Actual sleep = REM + Deep + Light (not in-bed time)
            if rem_min is not None or deep_min is not None or light_min is not None:
                actual_sleep_min = (rem_min or 0) + (deep_min or 0) + (light_min or 0)
            sn = ss.get("sleep_needed", {})
            if sn:
                sleep_need_base_min = round(sn.get("baseline_milli", 0) / 60_000) if sn.get("baseline_milli") else None
                sleep_need_debt_min = round(sn.get("need_from_sleep_debt_milli", 0) / 60_000) if sn.get("need_from_sleep_debt_milli") else None
                sleep_need_strain_min = round(sn.get("need_from_recent_strain_milli", 0) / 60_000) if sn.get("need_from_recent_strain_milli") else None

        # Cycle / Strain
        strain = None
        kilojoule = None
        avg_hr = None
        max_hr = None
        if cycle:
            cs = cycle.get("score", {})
            strain_raw = cs.get("strain")
            strain = round(strain_raw, 1) if strain_raw is not None else None
            kilojoule_raw = cs.get("kilojoule")
            kilojoule = round(kilojoule_raw) if kilojoule_raw is not None else None
            avg_hr = cs.get("average_heart_rate")
            max_hr = cs.get("max_heart_rate")

        # Body
        weight = None
        body_fat = None
        if body:
            w = body.get("weight_kilogram") or body.get("body_mass_kg")
            weight = round(w, 1) if w else None
            bf = body.get("body_fat_percentage")
            body_fat = round(bf, 1) if bf else None

        # Workouts
        boxing = False
        workout_count = 0
        workout_strain_total = 0
        workout_lines = []
        if workouts:
            workout_count = len(workouts)
            for wo in workouts:
                ws = wo.get("score", {})
                sport = wo.get("sport_name", "Unknown")
                if sport.lower() in ("boxing", "kickboxing", "martial arts"):
                    boxing = True
                wo_strain = ws.get("strain")
                if wo_strain:
                    workout_strain_total = round(workout_strain_total + wo_strain, 1)
                start_str = wo.get("start", "")
                end_str = wo.get("end", "")
                try:
                    start_t = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(TZ).strftime("%H:%M")
                    end_t = datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone(TZ).strftime("%H:%M")
                    time_range = f"{start_t}-{end_t}"
                except:
                    time_range = ""
                parts = [time_range, sport]
                if wo_strain:
                    parts.append(f"Strain: {round(wo_strain, 1)}")
                if ws.get("average_heart_rate"):
                    parts.append(f"Avg HR: {ws['average_heart_rate']}")
                if ws.get("max_heart_rate"):
                    parts.append(f"Max HR: {ws['max_heart_rate']}")
                if ws.get("kilojoule"):
                    parts.append(f"{round(ws['kilojoule'], 1)} kJ")
                workout_lines.append("- " + " | ".join(p for p in parts if p))
        # Fallback: detect boxing by strain threshold if no workout data
        if not workouts and strain is not None and strain >= 5:
            boxing = True

        # === Build YAML frontmatter ===
        def v(val):
            return "null" if val is None else str(val)

        def vbool(val):
            return "true" if val else "false"

        fm = [
            "---",
            f"date: {today}",
            f"recovery: {v(recovery)}",
            f"recovery_state: {recovery_state or 'null'}",
            f"rhr: {v(rhr)}",
            f"hrv: {v(hrv)}",
            f"spo2: {v(spo2)}",
            f"skin_temp: {v(skin_temp)}",
            f"in_bed_hours: {v(in_bed_hours)}",
            f"actual_sleep_min: {v(actual_sleep_min)}",
            f"sleep_perf: {v(sleep_perf)}",
            f"sleep_eff: {v(sleep_eff)}",
            f"sleep_consistency: {v(sleep_consistency)}",
            f"respiratory_rate: {v(respiratory_rate)}",
            f"rem_min: {v(rem_min)}",
            f"deep_min: {v(deep_min)}",
            f"light_min: {v(light_min)}",
            f"awake_min: {v(awake_min)}",
            f"disturbances: {v(disturbances)}",
            f"sleep_need_base_min: {v(sleep_need_base_min)}",
            f"sleep_need_debt_min: {v(sleep_need_debt_min)}",
            f"sleep_need_strain_min: {v(sleep_need_strain_min)}",
            f"strain: {v(strain)}",
            f"kilojoule: {v(kilojoule)}",
            f"avg_hr: {v(avg_hr)}",
            f"max_hr: {v(max_hr)}",
            f"weight: {v(weight)}",
            f"body_fat: {v(body_fat)}",
            f"boxing: {vbool(boxing)}",
            f"workout_count: {workout_count}",
            f"workout_strain_total: {v(workout_strain_total if workout_count else None)}",
            "---",
        ]

        # === Build human-readable body ===
        body_lines = []
        if recovery is not None:
            line = f"Recovery: {recovery}% ({recovery_state})"
            if hrv is not None:
                line += f" | HRV: {hrv} ms | RHR: {rhr} bpm"
            body_lines.append(line)
        if in_bed_hours is not None:
            actual_h = round(actual_sleep_min / 60, 1) if actual_sleep_min else in_bed_hours
            line = f"Sleep: {actual_h}h (perf {sleep_perf}%, eff {sleep_eff}%)"
            if rem_min is not None:
                line += f" | REM: {rem_min} min, Deep: {deep_min} min"
            body_lines.append(line)
        if strain is not None:
            line = f"Strain: {strain}"
            if boxing:
                line += " (бокс)"
            body_lines.append(line)
        if weight is not None:
            body_lines.append(f"Weight: {weight} kg")

        if workout_lines:
            body_lines.append("")
            body_lines.append("## Workouts")
            body_lines.extend(workout_lines)

        return "\n".join(fm) + "\n\n" + "\n".join(body_lines) + "\n"

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
