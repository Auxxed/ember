from datetime import datetime


class PuffcoUtils:
    @staticmethod
    def revision_number_to_string(value) -> str:
        rev_letters = "ABCDEFGHJKMNPRTUVWXYZ"
        if not isinstance(value, int) or value < 0:
            return str(value)
        if value == 0:
            return "X*"
        shift = value - 1
        out = ""
        while shift >= 0:
            out = rev_letters[shift % len(rev_letters)] + out
            shift = shift // len(rev_letters) - 1
        return out

    @staticmethod
    def c_to_f(celsius: float) -> int:
        return int(round((float(celsius) * 1.8) + 32))

    @staticmethod
    def f_to_c(fahrenheit: float) -> float:
        return (float(fahrenheit) - 32) / 1.8

    @staticmethod
    def c_string(raw) -> str:
        """Decode a device C-string. The name buffers are fixed-size and a
        shorter write leaves the old tail after the first NUL."""
        if raw is None:
            return ""
        if isinstance(raw, str):
            text = raw
        else:
            text = bytes(raw).decode(errors="ignore")
        return text.split("\x00", 1)[0].strip()

    @staticmethod
    def format_uptime(seconds: int) -> str:
        seconds = max(0, int(seconds))
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        if days:
            return f"{days}d {hours}h {minutes}m"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {secs}s"

    @staticmethod
    def format_birthday(unix: int | None) -> str:
        """Device first-use date from `/u/sys/bday`."""
        try:
            stamp = int(unix)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return ""
        if stamp <= 0:
            return ""
        return datetime.fromtimestamp(stamp).strftime("%b %d, %Y").replace(" 0", " ")
