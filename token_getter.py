"""
Discord Token Getter - Optimized for Railway
"""

import os
import time
from typing import Optional

def get_discord_token(email: str, password: str) -> Optional[str]:
    """
    Get Discord token using email/password
    Simplified for Railway
    """
    try:
        # Try to import selenium
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        
        print(f"🔄 Attempting to login as {email}")
        
        # Setup Chrome options for Railway
        chrome_options = Options()
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        
        # Try to find Chrome binary
        chrome_binary = None
        possible_paths = [
            '/usr/bin/chromium',
            '/usr/bin/chromium-browser',
            '/usr/bin/google-chrome',
            '/usr/bin/chrome'
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                chrome_binary = path
                break
        
        if chrome_binary:
            chrome_options.binary_location = chrome_binary
            print(f"✅ Found Chrome at: {chrome_binary}")
        else:
            print("⚠️ Chrome binary not found, using default")
        
        # Setup service
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        
        service = Service(ChromeDriverManager().install())
        
        # Create driver
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        try:
            # Login to Discord
            driver.get("https://discord.com/login")
            time.sleep(3)
            
            # Enter email
            email_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, "email"))
            )
            email_field.send_keys(email)
            time.sleep(1)
            
            # Enter password
            password_field = driver.find_element(By.NAME, "password")
            password_field.send_keys(password)
            time.sleep(1)
            
            # Click login
            login_button = driver.find_element(By.XPATH, "//button[@type='submit']")
            login_button.click()
            
            # Wait for login
            time.sleep(10)
            
            # Get token from localStorage
            token = driver.execute_script("""
                // Try multiple methods to get token
                let token = localStorage.getItem('token');
                if (!token) {
                    // Try all localStorage keys
                    for (let i = 0; i < localStorage.length; i++) {
                        let key = localStorage.key(i);
                        let value = localStorage.getItem(key);
                        if (value && (value.includes('mfa.') || value.length > 100)) {
                            token = value;
                            break;
                        }
                    }
                }
                return token ? token.replace(/"/g, '') : null;
            """)
            
            if token and (token.startswith('mfa.') or len(token) > 50):
                print(f"✅ Successfully got token")
                return token
            else:
                print("❌ No valid token found in localStorage")
                
                # Try alternative method
                token = driver.execute_script("""
                    return document.cookie.split(';').find(c => c.includes('token')) || '';
                """)
                
                if token:
                    token = token.split('=')[1].strip()
                    return token
                else:
                    return None
                    
        finally:
            driver.quit()
            
    except ImportError as e:
        print(f"❌ Selenium import error: {e}")
        print("📦 Try: pip install selenium webdriver-manager")
        return None
    except Exception as e:
        print(f"❌ Error getting token: {e}")
        return None

# Test function
if __name__ == "__main__":
    print("🔧 Discord Token Getter - Test")
    print("=" * 40)
    
    # Get from env or input
    email = os.getenv('TEST_EMAIL', '')
    password = os.getenv('TEST_PASSWORD', '')
    
    if not email:
        email = input("Email: ").strip()
    if not password:
        password = input("Password: ").strip()
    
    if email and password:
        token = get_discord_token(email, password)
        if token:
            print(f"\n✅ Token: {token[:50]}...")
        else:
            print("\n❌ Failed to get token")
    else:
        print("❌ Email and password required")
