import json
import time
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

import adspower
from config import load_config
import human

def run_debug_mobile():
    # 1. Загружаем конфиг
    cfg = load_config()
    
    # 2. Проверяем, задан ли мобильный профиль
    mobile_user_id = cfg.get("mobile_user_id")
    if not mobile_user_id:
        print("❌ ОШИБКА: Не задан 'mobile_user_id' в config.py или .env")
        print("Пожалуйста, создайте мобильный профиль в AdsPower и укажите его ID.")
        return

    # Подменяем user_id на мобильный для подключения
    cfg["user_id"] = mobile_user_id
    
    print(f"🚀 Запускаем мобильный профиль: {mobile_user_id}")
    
    # 3. Получаем WebSocket URL от AdsPower
    ws_url = adspower.get_ws_url(cfg)
    if not ws_url:
        print("❌ Не удалось получить WebSocket URL. Проверьте, запущен ли AdsPower.")
        return

    print(f"🔗 Подключаемся к браузеру...")

    with sync_playwright() as p:
        try:
            # Увеличим timeout для подключения
            browser = p.chromium.connect_over_cdp(ws_url, timeout=20000)
            
            # Проверяем контексты, если нет - создаем
            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = browser.new_context(viewport={"width": 360, "height": 740}, user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")
            
            # Стараемся найти уже открытую вкладку или создаем новую
            if context.pages:
                page = context.pages[0]
            else:
                page = context.new_page()

            print("🌍 Переходим на m.facebook.com...")
            try:
                # Иногда m.facebook.com редиректит или долго грузится
                page.goto("https://m.facebook.com/", timeout=60000, wait_until="domcontentloaded")
            except Exception as e:
                 print(f"⚠️ Ошибка навигации (не критична, если уже открыто): {e}")
            
            # Файл для дампа
            dump_file = "mobile_debug_dump.jsonl"
            # Очистим старый дамп
            with open(dump_file, "w", encoding="utf-8") as f:
                f.write("")
            
            print(f"💾 Данные будут сохраняться в {dump_file}")

            # Функция для перехвата ответов
            def handle_response(response):
                try:
                    # Логируем ВСЕ для отладки
                    url = response.url
                    print(f"🌍 Response: {url[:60]}...")
                    
                    # Сохраняем ВСЕ JSON-ответы (не только graphql)
                    if response.request.method == "POST" or "api" in url or "ajax" in url:
                        try:
                            json_body = response.json()
                            line = json.dumps({
                                "url": url,
                                "method": response.request.method,
                                "status": response.status,
                                "body": json_body
                            }, ensure_ascii=False)
                            
                            with open(dump_file, "a", encoding="utf-8") as f:
                                f.write(line + "\n")
                            
                            print(f"   💾 Saved JSON response from: {url[:40]}...")
                                
                        except Exception:
                            pass
                            
                except Exception as e:
                    pass

            # Подписываемся на события
            page.on("response", handle_response)
            
            print("📸 Делаем скриншот перед скроллом...")
            try:
                page.screenshot(path="mobile_debug_screen.png")
            except Exception as e:
                print(f"⚠️ Скриншот не удался: {e}")

            print("scroll... (скроллим ленту 5-10 раз)")
            
            # Немного посроллим
            for i in range(5):
                print(f"👇 Скролл {i+1}/5")
                human.human_scroll(page, cfg)
                time.sleep(2)
            
            print("✅ Готово! Проверьте файл mobile_debug_dump.jsonl")
            print("Теперь мы можем проанализировать структуру ответов m.facebook.com")
            
            # Не закрываем браузер, чтобы пользователь мог посмотреть
            # browser.close()
            
        except Exception as e:
            print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    run_debug_mobile()
