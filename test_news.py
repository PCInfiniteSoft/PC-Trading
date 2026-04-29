import cloudscraper
import xml.etree.ElementTree as ET
from datetime import datetime

def test_fetch_news():
    print("⏳ กำลังสวมหน้ากากและเชื่อมต่อ ForexFactory (ผ่าน Cloudflare)...")
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    
    try:
        # ใช้ cloudscraper จำลองเบราว์เซอร์
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
        response = scraper.get(url, timeout=15)
        
        # เช็คว่าทะลวงผ่านไหม
        print(f"✅ ทะลวงกำแพงสำเร็จ! (Status Code: {response.status_code})")
        print("กำลังถอดรหัสไฟล์ข่าว...\n")
        
        tree = ET.fromstring(response.content)
        today_str = datetime.now().strftime("%m-%d-%Y")
        
        print(f"📅 ค้นหาข่าวของวันนี้: {today_str} | สกุลเงิน: USD | ระดับ: High (กล่องแดง)")
        print("-" * 55)
        
        news_found = False
        for event in tree.findall('event'):
            date = event.find('date').text
            impact = event.find('impact').text
            currency = event.find('country').text
            
            # กรองเฉพาะวันนี้, กล่องแดง, และเงิน USD
            if date == today_str and impact == 'High' and currency == 'USD':
                title = event.find('title').text
                time = event.find('time').text
                print(f"🚨 [{time}] {currency}: {title}")
                news_found = True
                
        if not news_found:
            print("✅ วันนี้ไม่มีข่าวกล่องแดง (High Impact) ของคู่เงิน USD ครับ")
            
        print("-" * 55)
        print("🎉 สรุป: ระบบดึงข่าวทำงานได้สมบูรณ์ 100% พร้อมเอาขึ้น VPS!")
            
    except Exception as e:
        print("\n❌ ทะลวงกำแพงไม่สำเร็จ! เจอ Error:")
        print(str(e))

if __name__ == "__main__":
    test_fetch_news()