import random
import time


def human_scroll(page, cfg: dict):
    steps = random.randint(cfg["scroll_steps_min"], cfg["scroll_steps_max"])
    for _ in range(steps):
        delta = random.randint(cfg["scroll_min_px"], cfg["scroll_max_px"])
        page.mouse.wheel(0, delta)
        if random.random() < 0.3:
            page.mouse.move(
                random.randint(100, 900),
                random.randint(100, 700),
                steps=random.randint(4, 10),
            )
        time.sleep(random.uniform(cfg["min_scroll_pause"], cfg["max_scroll_pause"]))
        if random.random() < 0.15:
            time.sleep(random.uniform(cfg["min_idle_pause"], cfg["max_idle_pause"]))
    if random.random() < 0.2:
        page.mouse.wheel(0, -random.randint(80, 220))
        time.sleep(random.uniform(0.2, 0.5))


def human_idle(page, cfg: dict):
    if random.random() < 0.4:
        page.mouse.move(
            random.randint(120, 900),
            random.randint(120, 720),
            steps=random.randint(6, 18),
        )
        time.sleep(random.uniform(cfg["min_hover_pause"], cfg["max_hover_pause"]))
    if random.random() < 0.5:
        time.sleep(random.uniform(cfg["min_idle_pause"], cfg["max_idle_pause"]))
