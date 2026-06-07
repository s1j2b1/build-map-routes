


import os
import csv
import math
import re
import time
from flask import Flask, render_template, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# إعداد تطبيق فلاسك
app = Flask(__name__)

# =====================================================================
# دوال مساعدة
# =====================================================================
def arab_txt(text):
    import arabic_reshaper
    from bidi.algorithm import get_display
    reshaped = arabic_reshaper.reshape(text)
    bidi_text = get_display(reshaped)
    return bidi_text

# =====================================================================
# المرحلة الأولى: كلاس استخراج البيانات وأتمتة المتصفح (Web Scraping)
# =====================================================================
# تم إخراج الكلاسات إلى النطاق العام (Global Scope) لكي تعمل بشكل صحيح مع Flask
class GoogleMapsScraper:
    """كلاس مسؤول عن أتمتة متصفح كروم واستخراج الإحداثيات."""

    def __init__(self):
        self.options = Options()
        
        # --- تعديلات هامة جداً لخوادم السحابة (Render / Linux) ---
        self.options.add_argument("--headless=new") # تشغيل المتصفح بدون شاشة (مخفي) وهو إجباري للسيرفر
        self.options.add_argument("--no-sandbox") # إيقاف وضع الحماية لتفادي أخطاء الصلاحيات في لينكس
        self.options.add_argument("--disable-dev-shm-usage") # حل مشكلة امتلاء الذاكرة (RAM) في السيرفر
        self.options.add_argument("--disable-gpu") # تعطيل كرت الشاشة (لأن السيرفر لا يملك واحداً)
        self.options.add_argument("--window-size=1920,1080")
        
        # تم إزالة مسار 'C:\Users\...' لأنه لا يوجد ويندوز في سيرفر Render
        self.driver = None

    def start_driver(self):
        try:
            # تثبيت مشغل الكروم تلقائياً
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=self.options)
            print("[+] المتصفح يعمل الآن في الخلفية (Headless Mode).")
        except Exception as e:
            print(f"[-] خطأ أثناء تشغيل المتصفح: {e}")
            raise e

    def extract_coords_from_url(self, url: str) -> tuple:
        regex_pattern = r"@([-+]?\d+\.\d+),([-+]?\d+\.\d+)"
        match = re.search(regex_pattern, url)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None, None

    def scrape_saved_list(self, list_url: str, output_csv: str = "locations.csv"):
        if not self.driver:
            self.start_driver()

        try:
            print(f"[+] الانتقال إلى القائمة: {list_url}")
            self.driver.get(list_url)
            time.sleep(5)

            elements = self.driver.find_elements(By.XPATH, "//a[contains(@href, '/maps/place/')]")
            place_urls = [elem.get_attribute("href") for elem in elements]

            if not place_urls:
                print("[-] لم يتم العثور على مواقع. قد تحتاج القائمة لتكون 'عامة' (Public).")
                return

            with open(output_csv, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["Location Name", "Latitude", "Longitude"])

                for idx, url in enumerate(place_urls, 1):
                    try:
                        self.driver.get(url)
                        WebDriverWait(self.driver, 15).until(lambda d: "@" in d.current_url)
                        lat, lng = self.extract_coords_from_url(self.driver.current_url)
                        place_name = self.driver.title.split(" - ")[0]

                        if lat and lng:
                            writer.writerow([place_name, lat, lng])
                        time.sleep(2)
                    except Exception as loc_error:
                        pass

        except Exception as e:
            print(f"[-] حدث خطأ: {e}")
        finally:
            if self.driver:
                self.driver.quit()

# =====================================================================
# المرحلة الثانية: كلاس الحسابات الجغرافية (Route Optimizer)
# =====================================================================
class RouteOptimizer:
    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = (math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def optimize_route(self, csv_file: str, current_loc: tuple) -> list:
        locations = []
        try:
            with open(csv_file, mode="r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    locations.append({
                        "name": row["Location Name"],
                        "lat": float(row["Latitude"]),
                        "lng": float(row["Longitude"]),
                    })
        except Exception:
            return []

        ordered_route = []
        current_lat, current_lng = current_loc

        while locations:
            closest_place = None
            min_distance = float("inf")
            for place in locations:
                dist = self.haversine_distance(current_lat, current_lng, place["lat"], place["lng"])
                if dist < min_distance:
                    min_distance = dist
                    closest_place = place
            ordered_route.append(closest_place)
            locations.remove(closest_place)
            current_lat, current_lng = closest_place["lat"], closest_place["lng"]

        return ordered_route

# =====================================================================
# المرحلة الثالثة: كلاس بناء المسار
# =====================================================================
class RoutePlotter:
    def generate_multi_stop_url(self, current_loc: tuple, ordered_route: list) -> str:
        base_url = "https://www.google.com/maps/dir/"
        url_parts = [f"{current_loc[0]},{current_loc[1]}"]
        for station in ordered_route:
            url_parts.append(f"{station['lat']},{station['lng']}")
        return base_url + "/".join(url_parts)


# =====================================================================
# مسارات تطبيق الويب (Flask Routes)
# =====================================================================

@app.route('/')
def index():
    # تأكد من وجود ملف indexx.html داخل مجلد اسمه templates
    return render_template('indexx.html')

@app.route('/generate', methods=['POST'])
def generate():
    try:
        # إعدادات المستخدم (يمكنك تعديل الإحداثيات الحالية من هنا)
        GOOGLE_SAVED_LIST_URL = r"https://maps.app.goo.gl/yMYr7c6LAEiWXqVt9"
        MY_CURRENT_LAT_LNG = (23.629079, 58.197913)
        CSV_FILE_NAME = "my_optimized_destinations.csv"

        # -------------------------------------------------------------
        # الخطوة 1: توليد البيانات (أبقيت لك البيانات الوهمية لضمان عمل التطبيق حالياً)
        # يمكنك حذف إنشاء الملف الوهمي واستخدام scraper.scrape_saved_list عند الحاجة
        # -------------------------------------------------------------
        with open(CSV_FILE_NAME, mode="w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Location Name", "Latitude", "Longitude"])
            w.writerow(["شركة وصل 1", 23.575167, 58.398224])
            w.writerow(["شركة وصل 2", 23.557165, 58.401804])
            w.writerow(["شركة وصل 3", 23.588159, 58.357191])

        # -------------------------------------------------------------
        # الخطوة 2: الترتيب
        # -------------------------------------------------------------
        optimizer = RouteOptimizer()
        optimized_stations = optimizer.optimize_route(csv_file=CSV_FILE_NAME, current_loc=MY_CURRENT_LAT_LNG)

        # -------------------------------------------------------------
        # الخطوة 3: توليد الرابط النهائي وإرساله للواجهة
        # -------------------------------------------------------------
        plotter = RoutePlotter()
        smart_url = plotter.generate_multi_stop_url(current_loc=MY_CURRENT_LAT_LNG, ordered_route=optimized_stations)

        # استخدام jsonify لإرجاع النتيجة كبيانات يفهمها متصفح المستخدم
        return jsonify({"url": smart_url, "status": "success"})

    except Exception as e:
        # في حال حدوث خطأ، يتم إرجاعه للواجهة
        return jsonify({"error": str(e), "status": "failed"}), 500

# =====================================================================
# تشغيل السيرفر
# =====================================================================
if __name__ == '__main__':
    # تحديد المنفذ (Port) بشكل ديناميكي لكي يعمل على Render أو على جهازك
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)


application = app
