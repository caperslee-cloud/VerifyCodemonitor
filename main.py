#!/usr/bin/env python3
"""
163邮箱验证码转发到Telegram - 修复版
修复了IMAP状态错误
"""

import os
import time
import imaplib
import email
import re
import requests
import logging
from email.header import decode_header
from datetime import datetime

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

print("=" * 60)
print("📧 163邮箱 → Telegram 验证码转发服务")
print("版本: 修复IMAP状态错误版")
print("=" * 60)

class FixedEmailMonitor:
    def __init__(self):
        # 从环境变量读取配置
        self.email = os.environ.get("EMAIL_163", "").strip()
        self.password = os.environ.get("PASSWORD_163", "").strip()
        self.bot_token = os.environ.get("BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("CHAT_ID", "").strip()
        
        # 邮箱服务器配置
        self.imap_server = "imap.exmail.qq.com"
        self.imap_port = 993
        
        # 检查间隔（秒）
        self.check_interval = 10
        
        # 验证配置
        self.check_config()
    
    def check_config(self):
        """检查配置是否完整"""
        required = {
            "EMAIL_163": self.email,
            "PASSWORD_163": self.password,
            "BOT_TOKEN": self.bot_token,
            "CHAT_ID": self.chat_id
        }
        
        missing = []
        for key, value in required.items():
            if not value:
                missing.append(key)
        
        if missing:
            logger.error(f"❌ 缺少环境变量: {', '.join(missing)}")
            logger.error("请在Koyeb的Environment Variables中设置")
            exit(1)
        
        logger.info("✅ 配置检查通过")
        logger.info(f"📧 监控邮箱: {self.email}")
    
    def connect_and_select(self):
        """连接邮箱并选择收件箱 - 修复的关键函数"""
        try:
            logger.debug("正在连接163邮箱服务器...")
            
            # 1. 建立SSL连接
            mail = imaplib.IMAP4_SSL(
                host=self.imap_server,
                port=self.imap_port,
                timeout=15
            )
            
            # 2. 登录
            logger.debug("正在登录...")
            mail.login(self.email, self.password)
            
            # 3. ✅ 关键修复：必须先选择文件夹！
            logger.debug("正在选择收件箱...")
            status, data = mail.select("INBOX")
            
            if status != "OK":
                logger.error(f"❌ 选择收件箱失败: {data}")
                mail.logout()
                return None
            
            logger.debug("✅ 邮箱连接成功")
            return mail
            
        except imaplib.IMAP4.error as e:
            logger.error(f"❌ IMAP登录失败: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ 连接失败: {e}")
            return None
    
    def search_unread_emails(self, mail):
        """搜索未读邮件 - 修复后的正确调用"""
        try:
            # ✅ 现在mail已经处于SELECTED状态，可以执行SEARCH
            status, messages = mail.search(None, 'UNSEEN')
            
            if status != "OK":
                logger.error(f"❌ 搜索邮件失败: {messages}")
                return []
            
            if not messages[0]:
                return []  # 没有新邮件
            
            email_ids = messages[0].split()
            return email_ids
            
        except Exception as e:
            logger.error(f"❌ 搜索邮件时出错: {e}")
            return []
    
    def fetch_email_content(self, mail, email_id):
        """获取邮件内容"""
        try:
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            
            if status != "OK":
                logger.error(f"❌ 获取邮件内容失败: {msg_data}")
                return None, None, None
            
            # 解析邮件
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # 提取标题
            subject_raw = msg.get("Subject", "无标题")
            subject = self.decode_subject(subject_raw)
            
            # 提取时间
            date_raw = msg.get("Date", "")
            if date_raw:
                # 尝试解析邮件时间
                try:
                    date_str = str(date_raw)
                except:
                    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 提取正文
            body = self.extract_email_body(msg)
            
            return subject, date_str, body
            
        except Exception as e:
            logger.error(f"❌ 解析邮件失败: {e}")
            return None, None, None
    
    def decode_subject(self, subject):
        """解码邮件标题"""
        try:
            decoded = decode_header(subject)
            result = ""
            for content, charset in decoded:
                if isinstance(content, bytes):
                    result += content.decode(charset if charset else 'utf-8', errors='ignore')
                else:
                    result += str(content)
            return result.strip()
        except:
            return str(subject)
    
    def extract_email_body(self, msg):
        """提取邮件正文（纯文本）"""
        body = ""
        
        try:
            if msg.is_multipart():
                # 多部分邮件
                for part in msg.walk():
                    content_type = part.get_content_type()
                    
                    # 只取纯文本部分
                    if content_type == "text/plain":
                        try:
                            body_bytes = part.get_payload(decode=True)
                            if body_bytes:
                                body = body_bytes.decode('utf-8', errors='ignore')
                                break
                        except:
                            continue
            else:
                # 单部分邮件
                body_bytes = msg.get_payload(decode=True)
                if body_bytes:
                    body = body_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"提取正文失败: {e}")
        
        return body
    
    def extract_verification_code(self, text):
        """从文本提取验证码"""
        if not text:
            return None
        
        # 常见验证码模式
        patterns = [
            r'验证码[：:]\s*(\d{4,8})',
            r'【.*?】\s*(\d{4,8})',
            r'code[：:]\s*(\d{4,8})',
            r'verification[：:]\s*(\d{4,8})',
            r'\b(\d{6})\b',  # 6位数字
            r'\b(\d{4})\b',  # 4位数字
        ]
        
        # 只搜索前500字符
        search_text = text[:500]
        
        for pattern in patterns:
            match = re.search(pattern, search_text, re.IGNORECASE)
            if match:
                code = match.group(1) if match.groups() else match.group(0)
                if code.isdigit() and 4 <= len(code) <= 8:
                    logger.debug(f"找到验证码: {code} (模式: {pattern})")
                    return code
        
        return None
    
    def send_to_telegram(self, subject, date, code=None):
        """发送通知到Telegram"""
        try:
            # 构建消息
            emoji = "🔐" if code else "📧"
            
            message = f"{emoji} *邮箱通知*\n\n"
            message += f"📋 *标题*: {subject}\n\n"
            message += f"⏰ *时间*: {date}\n"
            
            if code:
                message += f"\n🔢 *验证码*: `{code}`\n"
            
            message += "\n📬 自动监控服务"
            
            # Telegram API URL
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
                "disable_notification": False,
            }
            
            # 发送请求
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"✅ Telegram发送成功: {subject[:30]}...")
                return True
            else:
                logger.error(f"❌ Telegram错误: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 发送到Telegram失败: {e}")
            return False
    
    def mark_as_read(self, mail, email_id):
        """标记邮件为已读"""
        try:
            mail.store(email_id, '+FLAGS', '\\Seen')
            logger.debug(f"邮件标记为已读: {email_id}")
            return True
        except Exception as e:
            logger.error(f"标记已读失败: {e}")
            return False
    
    def process_single_email(self):
        """处理单次邮箱检查"""
        mail = None
        try:
            # 1. 连接并选择文件夹
            mail = self.connect_and_select()
            if not mail:
                return False
            
            # 2. 搜索未读邮件
            email_ids = self.search_unread_emails(mail)
            
            if not email_ids:
                logger.debug("📭 没有新邮件")
                return True
            
            logger.info(f"📨 发现 {len(email_ids)} 封新邮件")
            
            # 3. 处理每封邮件（从最新开始）
            for email_id in email_ids[-3:]:  # 只处理最新3封
                try:
                    # 获取邮件内容
                    subject, date, body = self.fetch_email_content(mail, email_id)
                    
                    if not subject:
                        continue
                    
                    logger.info(f"📧 处理邮件: {subject[:40]}...")
                    
                    # 提取验证码
                    code = self.extract_verification_code(body)
                    
                    # 发送到Telegram
                    success = self.send_to_telegram(subject, date, code)
                    
                    if success:
                        # 标记为已读
                        self.mark_as_read(mail, email_id)
                    
                except Exception as e:
                    logger.error(f"处理邮件失败: {e}")
                    continue
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 处理邮箱时出错: {e}")
            return False
            
        finally:
            # 确保关闭连接
            if mail:
                try:
                    mail.close()
                    mail.logout()
                except:
                    pass
    
    def run(self):
        """主运行循环"""
        logger.info("🚀 服务启动成功！")
        logger.info(f"📧 监控邮箱: {self.email}")
        logger.info(f"⏰ 检查间隔: {self.check_interval}秒")
        logger.info("=" * 50)
        
        error_count = 0
        max_errors = 5
        
        while True:
            try:
                cycle_start = time.time()
                
                # 处理邮箱
                success = self.process_single_email()
                
                if success:
                    error_count = 0
                else:
                    error_count += 1
                    logger.warning(f"⚠️ 处理失败 ({error_count}/{max_errors})")
                
                # 错误过多时等待更久
                if error_count >= max_errors:
                    logger.error("❌ 连续错误过多，等待60秒后重试...")
                    time.sleep(60)
                    error_count = 0
                    continue
                
                # 计算等待时间
                cycle_time = time.time() - cycle_start
                sleep_time = max(1, self.check_interval - cycle_time)
                
                logger.debug(f"⏱️  本次循环用时: {cycle_time:.1f}秒，等待: {sleep_time:.1f}秒")
                time.sleep(sleep_time)
                
            except KeyboardInterrupt:
                logger.info("👋 服务停止")
                break
            except Exception as e:
                logger.error(f"❌ 主循环错误: {e}")
                time.sleep(30)

def main():
    """程序入口"""
    try:
        monitor = FixedEmailMonitor()
        monitor.run()
    except SystemExit:
        # 配置错误退出
        logger.error("程序因配置错误退出，等待Koyeb重启...")
        time.sleep(30)
    except Exception as e:
        logger.error(f"程序启动失败: {e}")
        time.sleep(30)

if __name__ == "__main__":
    main()
