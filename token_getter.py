"""
Discord Token Getter - Optimized for Railway
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import time
import os

def setup_chrome_options():
    """Setup Chrome options for Railway"""
    chrome_options = Options()
    
    # Railway requires these
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--headless')  # Headless for server
    
    # Prevent detection
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # For Railway's Chrome
    if os.path.exists('/usr/bin/chromium'):
        chrome_options.binary_location = '/usr/bin/chromium'
    elif os.path.exists('/usr/bin/chromium-browser'):
        chrome_options.binary_location = '/usr/bin/chromium-browser'
    
    return chrome_options

def get_discord_token(email: str, password: str):
    """
    Get Discord token using email/password
    Optimized for Railway deployment
    """
    driver = None
    try:
        print(f"🔄 Attempting to login as {email}")
        
        # Setup driver
        chrome_options = setup_chrome_options()
        
        # Use Chromium from Railway
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Hide automation
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # Login to Discord
        driver.get("https://discord.com/login")
        time.sleep(3)
        
        # Enter credentials
        email_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        email_field.send_keys(email)
        time.sleep(1)
        
        password_field = driver.find_element(By.NAME, "password")
        password_field.send_keys(password)
        time.sleep(1)
        
        # Click login
        login_button = driver.find_element(By.XPATH, "//button[@type='submit']")
        login_button.click()
        
        # Wait for login
        time.sleep(8)
        
        # Get token
        token = driver.execute_script("""
            // Try to get token from localStorage
            let token = localStorage.getItem('token');
            if (!token) {
                // Try alternative keys
                const keys = Object.keys(localStorage);
                for (let key of keys) {
                    if (key.includes('token') || localStorage[key].includes('mfa.')) {
                        token = localStorage[key];
                        break;
                    }
                }
            }
            return token ? token.replace(/"/g, '') : null;
        """)
        
        driver.quit()
        
        if token and (token.startswith('mfa.') or len(token) > 50):
            print(f"✅ Successfully got token")
            return token
        else:
            print("❌ No valid token found")
            return None
            
    except Exception as e:
        print(f"❌ Error: {e}")
        if driver:
            try:
                driver.quit()
            except:
                pass
        return None

# Simple test function
if __name__ == "__main__":
    # For testing only
    print("🔧 Discord Token Getter - Test Mode")
    print("=" * 40)
    
    # Get credentials from environment or input
    email = os.getenv('TEST_EMAIL', '')
    password = os.getenv('TEST_PASSWORD', '')
    
    if not email or not password:
        print("❌ Set TEST_EMAIL and TEST_PASSWORD environment variables")
        print("Or run from bot using !autotoken command")
    else:
        token = get_discord_token(email, password)
        if token:
            print(f"\n✅ Token (first 30 chars): {token[:30]}...")
        else:
            print("\n❌ Failed to get token")
