"""Owner-provided aggregate chat counts. No usernames or message contents are stored."""
import bisect
import math


def validate(rows):
    if not isinstance(rows, list) or len(rows) > 3600:
        raise ValueError("Provide up to 3,600 chat-count samples.")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each sample needs seconds and count.")
        seconds, count = row.get("seconds"), row.get("count")
        if (type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 86400
                or type(count) is not int or not 0 <= count <= 100000):
            raise ValueError("Chat times or counts are outside the allowed range.")
        result[round(seconds, 2)] = count
    return sorted(result.items())


def signal(samples, offset=0):
    if not samples:
        return None
    times, counts = zip(*samples)
    ordered = sorted(counts)
    typical = ordered[(len(ordered) - 1) // 2]
    threshold = max(3, typical * 2 + 2)

    def spike(start, end):
        low = bisect.bisect_left(times, start + offset)
        high = bisect.bisect_right(times, end + offset)
        return any(x >= threshold for x in counts[low:high])

    return spike
