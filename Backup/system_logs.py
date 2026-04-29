import os
import logging
from datetime import datetime
import shared_state

class DailyFileHandler(logging.FileHandler):
    def __init__(self, base_name, mode='a', encoding='utf-8', delay=False):
        self.base_name = base_name.replace(".txt", "")
        self.current_date = datetime.now().strftime("%Y_%m_%d")
        filename = f"{self.base_name}_{self.current_date}.txt"
        super().__init__(filename, mode, encoding, delay)

    def emit(self, record):
        new_date = datetime.fromtimestamp(record.created).strftime("%Y_%m_%d")
        if self.current_date != new_date:
            self.current_date = new_date
            self.close() 
            self.baseFilename = os.path.abspath(f"{self.base_name}_{self.current_date}.txt")
            self.stream = self._open()
        super().emit(record)

class GUIHandler(logging.Handler):
    def __init__(self, log_name=""):
        super().__init__()
        self.log_name = f"[{log_name}] " if log_name else ""
    def emit(self, record):
        msg = self.format(record)
        display_msg = f"{self.log_name}{msg}"
        shared_state.log_queue.put((display_msg, record.levelname))

class LogsFormatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        if msg and msg[0] in ["📊", "✅", "❌", "💰", "🚨", "⚠️", "📰", "🛡️", "⏳"]:
            time_str = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
            parts = msg.split(" ", 1)
            if len(parts) == 2:
                return f"{parts[0]} [{time_str}] {parts[1]}"
            return f"{msg} [{time_str}]"
        elif msg and msg[0] == "🔄":
            return msg 
        time_str_default = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        return f"[{time_str_default}] {msg}"

def setup_global_logging():
    if not os.path.exists("Logs"):
        os.makedirs("Logs")

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("discord").setLevel(logging.WARNING)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_gui = GUIHandler()
    root_gui.setFormatter(logging.Formatter('%(message)s'))
    root_logger.addHandler(root_gui)

    def create_rotating_logger(name, filename):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        
        fh = DailyFileHandler(filename, encoding='utf-8')
        fh.setFormatter(LogsFormatter()) 
        logger.addHandler(fh)
        
        gh = GUIHandler(name)
        gh.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(gh)
        
    create_rotating_logger("System", "Logs/PCTrading_log.txt")
    create_rotating_logger("BTCUSDm", "Logs/BTCUSDm_log.txt")
    create_rotating_logger("XAUUSDm", "Logs/XAUUSDm_log.txt")