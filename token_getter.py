"""
Auto Discord Token Getter using Selenium
Requires: pip install selenium webdriver-manager
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import json
import os

class DiscordTokenGetter:
    def __init__(self, headless=True):
        """Initialize Selenium with Chrome"""
        self.headless = headless
        self.setup_driver()
    
    def setup_driver(self):
        """Setup Chrome driver"""
        chrome_options = webdriver.ChromeOptions()
        
        if self.headless:
            chrome_options.add_argument('--headless')
        
        # Prevent detection
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-gpu')
        
        # For Railway/Replit
        if os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('REPLIT_DB_URL'):
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.binary_location = '/usr/bin/chromium-browser'
        
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Hide automation
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    def get_token(self, email: str, password: str, timeout=30):
        """
        Get Discord token using email/password
        Returns: token or None if failed
        """
        try:
            print(f"🔄 Logging into Discord with {email}...")
            
            # Open Discord login
            self.driver.get("https://discord.com/login")
            time.sleep(3)
            
            # Enter email
            email_field = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.NAME, "email"))
            )
            email_field.clear()
            email_field.send_keys(email)
            time.sleep(1)
            
            # Enter password
            password_field = self.driver.find_element(By.NAME, "password")
            password_field.clear()
            password_field.send_keys(password)
            time.sleep(1)
            
            # Click login
            login_button = self.driver.find_element(
                By.XPATH, "//button[@type='submit']"
            )
            login_button.click()
            
            # Wait for login (check for 2FA)
            time.sleep(8)
            
            # Check if 2FA is required
            try:
                auth_field = self.driver.find_element(By.NAME, "code")
                print("⚠️ 2FA detected! Please check your authenticator app.")
                print("Enter 2FA code manually in browser window...")
                
                # Keep browser open for manual 2FA
                print("⏳ Waiting for manual 2FA input (60 seconds)...")
                time.sleep(60)
            except:
                # No 2FA
                pass
            
            # Get token from localStorage
            token = self.driver.execute_script("""
                // Try multiple localStorage keys
                const keys = ['token', 'access_token'];
                for (const key of keys) {
                    const value = localStorage.getItem(key);
                    if (value) return value.replace(/"/g, '');
                }
                
                // Try from indexedDB
                return new Promise((resolve) => {
                    const request = indexedDB.open('keyval-store', 1);
                    request.onsuccess = function(event) {
                        const db = event.target.result;
                        const transaction = db.transaction('keyval');
                        const store = transaction.objectStore('keyval');
                        const getRequest = store.get('token');
                        getRequest.onsuccess = function() {
                            resolve(getRequest.result || '');
                        };
                        getRequest.onerror = function() {
                            resolve('');
                        };
                    };
                    request.onerror = function() {
                        resolve('');
                    };
                });
            """)
            
            # Wait a bit more if token is empty
            if not token:
                time.sleep(5)
                token = self.driver.execute_script("return localStorage.getItem('token')")
            
            self.driver.quit()
            
            if token and token.startswith(('mfa.', 'ND', 'MT', 'OD')):
                print(f"✅ Token obtained: {token[:20]}...")
                return token
            else:
                print("❌ Failed to get token")
                return None
                
        except Exception as e:
            print(f"❌ Error getting token: {e}")
            try:
                self.driver.save_screenshot('error_screenshot.png')
                print("📸 Screenshot saved as error_screenshot.png")
            except:
                pass
            self.driver.quit()
            return None
    
    def get_token_from_browser(self):
        """
        Get token from already logged-in browser session
        Useful if you're already logged in
        """
        try:
            self.driver.get("https://discord.com/channels/@me")
            time.sleep(3)
            
            # Get token
            token = self.driver.execute_script("""
                return window.localStorage.getItem('token');
            """)
            
            if token:
                token = token.replace(/"/g, '')
            
            self.driver.quit()
            return token
            
        except Exception as e:
            print(f"Error: {e}")
            self.driver.quit()
            return None

# ========== USAGE EXAMPLES ==========
def main():
    """Example usage"""
    print("🤖 Discord Token Getter")
    print("=" * 40)
    
    # Method 1: With credentials
    email = input("Enter Discord email: ").strip()
    password = input("Enter Discord password: ").strip()
    
    if email and password:
        getter = DiscordTokenGetter(headless=False)  # Set False to see browser
        token = getter.get_token(email, password)
        
        if token:
            print(f"\n✅ Your token: {token}")
            
            # Save to file
            with open('discord_token.txt', 'w') as f:
                f.write(token)
            print("📁 Token saved to discord_token.txt")
            
            # Also save to env file for bot
            with open('.env', 'a') as f:
                f.write(f"\nDISCORD_USER_TOKEN={token}\n")
            print("⚙️ Token added to .env file")
        else:
            print("❌ Failed to get token")
    
    # Method 2: From existing session
    # getter = DiscordTokenGetter(headless=False)
    # token = getter.get_token_from_browser()
    # print(f"Token: {token}")

if __name__ == "__main__":
    main()