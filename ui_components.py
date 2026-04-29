import customtkinter as ctk

class AssetCard(ctk.CTkFrame):
    def __init__(self, master, symbol_name):
        super().__init__(master, border_width=2, border_color="#34495e")
        self.symbol_name = symbol_name
        
        # 🟢 สร้างองค์ประกอบหน้าจอทั้งหมดไว้ในนี้
        ctk.CTkLabel(self, text=symbol_name, font=("Arial", 20, "bold"), text_color="#3498db").pack(pady=8)
        
        self.lbl_price = ctk.CTkLabel(self, text="Price: 0.00", font=("Consolas", 18, "bold"), text_color="#ffffff")
        self.lbl_price.pack(pady=2)
        
        self.lbl_market = ctk.CTkLabel(self, text="Market: Checking...", font=("Arial", 12, "bold"))
        self.lbl_market.pack(pady=2)
        
        self.lbl_regime = ctk.CTkLabel(self, text="Regime: N/A", font=("Arial", 14))
        self.lbl_regime.pack()
        
        self.lbl_rsi = ctk.CTkLabel(self, text="RSI: N/A", font=("Arial", 14))
        self.lbl_rsi.pack(pady=2)
        
        self.lbl_target = ctk.CTkLabel(self, text="Buy < -- | Sell > --", font=("Arial", 13), text_color="#2ecc71")
        self.lbl_target.pack(pady=5)
        
        self.lbl_wl_pl = ctk.CTkLabel(self, text="🏆 W:0 L:0 | 💰 P/L: 0.00 USD", font=("Arial", 12, "bold"), text_color="#3498db")
        self.lbl_wl_pl.pack(pady=2)
        
        self.lbl_thresh = ctk.CTkLabel(self, text="Volatility Threshold: 0.00", font=("Arial", 11), text_color="#95a5a6")
        self.lbl_thresh.pack(pady=5)

    # 🟢 ส่งคืนค่าเป็น Dictionary เพื่อให้โค้ดเก่าใน update_dashboard เอาไปใช้ต่อได้ทันทีโดยไม่ต้องแก้!
    def get_ui_elements(self):
        return {
            "price": self.lbl_price,
            "market": self.lbl_market,
            "regime": self.lbl_regime,
            "rsi": self.lbl_rsi,
            "target": self.lbl_target,
            "thresh": self.lbl_thresh,
            "wl_pl": self.lbl_wl_pl,
            "prev_price": 0.0
        }