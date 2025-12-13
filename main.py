#!/usr/bin/env python3
"""
QQ企业邮箱 → Telegram 验证码转发 (最终稳定版)
功能：1.精准识别中英文验证码邮件 2.完整显示原邮件标题 3.简洁消息格式 4.健康检查防休眠
"""

import os
import time
import imaplib
import email
import re
import requests
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from email.header import decode_header
from datetime import datetime
import pytz

# ========== 配置说明（在Koyeb环境变量中设置）==========
# 必需：
# 1. EMAIL: 你的完整企业邮箱地址
# 2. PASSWORD: 企业邮箱的客户端专用密码
# 3. BOT_TOKEN: 你的Telegram Bot Token
# 4. CHAT_ID: 你的Telegram Chat ID（支持多个，用逗号分隔）
# ==================================================

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ========== 1. 时区设置 ==========
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

def get_beijing_time():
    """获取当前北京时间（完整格式）"""
    now_utc = datetime.utcnow()
    now_beijing = pytz.utc.localize(now_utc).astimezone(BEIJING_TZ)
    return now_beijing.strftime('%Y-%m-%d %H:%M:%S')

def get_beijing_time_short():
    """获取当前北京时间（仅时间）"""
    now_utc = datetime.utcnow()
    now_beijing = pytz.utc.localize(now_utc).astimezone(BEIJING_TZ)
    return now_beijing.strftime('%H:%M:%S')

def parse_email_time(email_time_str):
    """解析邮件头时间并转换为北京时间（仅时间部分）"""
    if not email_time_str:
        return get_beijing_time_short()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(email_time_str)
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        beijing_time = dt.astimezone(BEIJING_TZ)
        return beijing_time.strftime('%H:%M:%S')
    except Exception:
        return get_beijing_time_short()

# ========== 2. 健康检查服务器（防休眠）==========
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        response = f"服务运行正常 | 北京时间: {get_beijing_time()}"
        self.wfile.write(response.encode())
    
    def log_message(self, format, *args):
        pass

def health_server():
    """启动健康检查服务器（端口8000）"""
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    logger.info(f"✅ 健康检查服务器已启动 | {get_beijing_time()}")
    server.serve_forever()

# ========== 3. 邮箱监控核心 ==========
class EmailMonitor:
    def __init__(self):
        # 固定配置：QQ企业邮箱服务器
        self.imap_server = "imap.exmail.qq.com"
        self.imap_port = 993
        
        # 从环境变量读取账号信息
        self.email = os.environ.get("EMAIL", "").strip()
        self.password = os.environ.get("PASSWORD", "").strip()
        self.bot_token = os.environ.get("BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("CHAT_ID", "").strip()
        
        # 内置关键词库（中英文全覆盖）
        self.keywords = [
            # 中文关键词
            "验证码", "校验码", "动态码", "安全码", "验证代码", 
            "登入码", "登录码", "确认码", "激活码", "验证口令",
            "一次性密码", "动态口令", "安全密钥", "授权码",
            
            # 英文关键词
            "verification code", "verification", "email code", 
            "security code", "login code", "access code", "one-time code",
            "otp", "email verification", "authentication code",
            "confirmation code", "activation code", "authorization code",
            
            # 通用代码关键词
            "code", "Code", "CODE", "验证", "verify"
        ]
        
        # 硬性排除关键词（绝对不转发）
        self.hard_exclude_keywords = [
            "日报", "周报", "月报", "财务报表", "业绩报告",
            "会议记录", "会议通知", "会议纪要", "会议邀请",
            "新闻稿", "通讯稿", "宣传稿", "活动通知",
            "发票", "账单", "收据", "报价单", "合同",
            "简历", "求职", "应聘", "招聘",
            "订阅", "Newsletter", "newsletter",
            "广告", "推广", "营销", "促销"
        ]
        
        logger.info(f"🔍 关键词数量: {len(self.keywords)} | 排除词数量: {len(self.hard_exclude_keywords)}")
        
        # 检查必需配置
        if not all([self.email, self.password, self.bot_token, self.chat_id]):
            logger.error("❌ 错误：请设置所有必需环境变量 (EMAIL, PASSWORD, BOT_TOKEN, CHAT_ID)")
            raise ValueError("缺少必要配置")
        
        logger.info("=" * 60)
        logger.info(f"📧 监控邮箱: {self.email}")
        logger.info(f"🔐 服务器: {self.imap_server}")
        logger.info(f"⏰ 系统时区: 北京时间 (UTC+8)")
        logger.info(f"🕛 服务启动时间: {get_beijing_time()}")
        logger.info("=" * 60)
    
    def decode_email_subject(self, subject_raw):
        """完整解码邮件标题，保持原始格式"""
        if not subject_raw:
            return "无标题"
        
        try:
            decoded_parts = decode_header(subject_raw)
            decoded_subject = ""
            
            for content, charset in decoded_parts:
                if isinstance(content, bytes):
                    try:
                        charset = charset if charset else 'utf-8'
                        decoded_subject += content.decode(charset, errors='ignore')
                    except:
                        decoded_subject += content.decode('utf-8', errors='ignore')
                else:
                    decoded_subject += str(content)
            
            return decoded_subject.strip()
        except Exception:
            return str(subject_raw).strip()
    
    def is_hard_excluded(self, subject):
        """检查是否为硬性排除的邮件类型"""
        subject_lower = subject.lower()
        for word in self.hard_exclude_keywords:
            if word.lower() in subject_lower:
                return True, word
        return False, None
    
    def contains_keywords(self, text):
        """检查文本是否包含任何关键词"""
        if not text:
            return False, None
        
        text_lower = text.lower()
        for keyword in self.keywords:
            if keyword.lower() in text_lower:
                return True, keyword
        return False, None
    
    def extract_verification_code(self, text):
        """从文本中提取验证码（支持多种格式）"""
        if not text:
            return None
        
        # 清理文本以便更好匹配
        clean_text = text.replace(' ', '').replace('\n', '').replace('\r', '')
        
        # 验证码匹配模式（按优先级排序）
        patterns = [
            # 标准格式：验证码：123456
            r'验证码[：:]\s*(\d{4,8})',
            r'校验码[：:]\s*(\d{4,8})',
            r'动态码[：:]\s*(\d{4,8})',
            r'安全码[：:]\s*(\d{4,8})',
            
            # 英文格式：code: 123456
            r'code[：:]\s*(\d{4,8})',
            r'Code[：:]\s*(\d{4,8})',
            r'CODE[：:]\s*(\d{4,8})',
            r'verification[：:]\s*(\d{4,8})',
            r'Verification[：:]\s*(\d{4,8})',
            
            # 括号格式：【123456】或[123456]
            r'[【\[\(](\d{4,8})[】\]\)]',
            
            # 纯数字验证码（6位最常见）
            r'(?<!\d)(\d{6})(?!\d)',
            r'(?<!\d)(\d{4})(?!\d)',
            r'(?<!\d)(\d{5})(?!\d)',
            r'(?<!\d)(\d{8})(?!\d)',
            
            # 带分隔符：123-456
            r'(\d{3}[-]\d{3})',
            r'(\d{2}[-]\d{2}[-]\d{2})',
            
            # 通用模式
            r'(\d{4,8})[^\d]{0,10}有效',
            r'(\d{4,8})[^\d]{0,10}验证',
        ]
        
        # 搜索范围：正文前1000字符
        search_text = text[:1000] + " " + clean_text[:500]
        
        for pattern in patterns:
            try:
                matches = re.findall(pattern, search_text, re.IGNORECASE)
                for match in matches:
                    code = match if isinstance(match, str) else match[0]
                    
                    # 验证码有效性检查
                    if self.is_valid_verification_code(code):
                        return code
            except Exception:
                continue
        
        return None
    
    def is_valid_verification_code(self, code):
        """验证是否为合理的验证码"""
        if not code or len(code) < 4 or len(code) > 8:
            return False
        
        # 排除常见无效数字
        invalid_codes = [
            '123456', '111111', '000000', '666666', '888888',
            '12345678', '11111111', '00000000',
            '1234', '1111', '0000',
        ]
        
        if code in invalid_codes:
            return False
        
        # 如果是纯数字，检查是否过于简单
        if code.isdigit():
            # 检查是否连续重复
            if len(set(code)) == 1:
                return False
            
            # 检查是否连续数字
            try:
                int_code = int(code)
                if int_code < 1000:
                    return False
            except:
                pass
        
        return True
    
    def should_process_email(self, subject, body):
        """
        判断是否处理邮件
        返回: (should_process, verification_code)
        """
        # 1. 检查是否硬性排除
        is_excluded, exclude_word = self.is_hard_excluded(subject)
        if is_excluded:
            logger.debug(f"邮件被排除: 标题含 '{exclude_word}'")
            return False, None
        
        # 2. 检查是否包含关键词（标题或正文）
        combined_text = (subject + " " + (body[:500] if body else ""))
        has_keyword, matched_keyword = self.contains_keywords(combined_text)
        
        if not has_keyword:
            logger.debug(f"邮件无关键词: {subject[:50]}...")
            return False, None
        
        # 3. 提取验证码
        verification_code = self.extract_verification_code(body if body else "")
        
        if verification_code:
            logger.debug(f"找到验证码: {verification_code} | 关键词: '{matched_keyword}'")
            return True, verification_code
        
        logger.debug(f"有关键词但无验证码: '{matched_keyword}'")
        return False, None
    
    def get_email_connection(self):
        """连接到QQ企业邮箱"""
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port, timeout=15)
            mail.login(self.email, self.password)
            mail.select("INBOX")
            return mail
        except imaplib.IMAP4.error as e:
            logger.error(f"❌ 邮箱登录失败: {e}")
            if "Invalid credentials" in str(e):
                logger.error("   可能原因: 1.密码错误 2.未使用客户端专用密码")
            return None
        except Exception as e:
            logger.error(f"❌ 连接邮箱失败: {e}")
            return None
    
    def fetch_email_content(self, mail, email_id):
        """获取邮件内容"""
        try:
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != "OK":
                return None
            
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # 提取标题（完整原始标题）
            subject_raw = msg.get("Subject", "")
            subject = self.decode_email_subject(subject_raw)
            
            # 提取邮件时间
            email_date = msg.get("Date", "")
            email_time = parse_email_time(email_date)
            
            # 提取正文（纯文本）
            body = ""
            try:
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        if content_type == "text/plain":
                            try:
                                body_bytes = part.get_payload(decode=True)
                                if body_bytes:
                                    body = body_bytes.decode('utf-8', errors='ignore')
                                    break
                            except:
                                continue
                else:
                    body_bytes = msg.get_payload(decode=True)
                    if body_bytes:
                        body = body_bytes.decode('utf-8', errors='ignore')
            except Exception:
                pass
            
            return {
                'id': email_id,
                'subject': subject,
                'body': body,
                'time': email_time
            }
            
        except Exception as e:
            logger.error(f"❌ 获取邮件内容失败: {e}")
            return None
    
    def extract_validity_info(self, subject, body):
        """提取有效期信息"""
        if not subject and not body:
            return None
        
        search_text = (subject + " " + (body[:200] if body else "")).lower()
        
        patterns = [
            r'(\d+[分分钟])内有效',
            r'有效期[为:]?(\d+[分分钟])',
            r'有效时间[为:]?(\d+[分分钟])',
            r'(\d+[小小时])内有效',
            r'valid for (\d+ minutes?)',
            r'expires in (\d+ minutes?)',
            r'validity: (\d+ minutes?)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, search_text)
            if match:
                time_unit = match.group(1)
                return f"{time_unit}内有效"
        
        return None
    
    def send_to_telegram(self, subject, verification_code, email_time, validity_info=None):
        """发送优化格式的消息到Telegram（完整显示标题，简洁格式）"""
        try:
            # 构建简洁消息格式
            message = "📨 验证码通知\n"
            message += "──────────────────\n"
            
            # 完整显示原始标题
            message += f"📌 标题：{subject}\n\n"
            message += f"🕒 时间：{email_time}\n"
            message += f"🔐 验证码：`{verification_code}`\n"
            
            # 只在有有效期信息时显示备注行
            if validity_info:
                message += f"📋 备注：{validity_info}\n"
            
            message += "──────────────────"
            
            # 支持多个Chat ID
            chat_ids = [cid.strip() for cid in self.chat_id.split(",") if cid.strip()]
            success_count = 0
            
            for chat_id in chat_ids:
                try:
                    url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                    payload = {
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    }
                    
                    response = requests.post(url, json=payload, timeout=10)
                    if response.status_code == 200:
                        logger.info(f"✅ 已发送到 Chat ID: {chat_id}")
                        success_count += 1
                    else:
                        logger.error(f"❌ 发送到 {chat_id} 失败: {response.text}")
                except Exception as e:
                    logger.error(f"❌ 发送到 {chat_id} 时出错: {e}")
            
            logger.info(f"📤 发送完成: {success_count}/{len(chat_ids)} 成功")
            return success_count > 0
                
        except Exception as e:
            logger.error(f"❌ 发送到Telegram时出错: {e}")
            return False
    
    def mark_email_as_read(self, mail, email_id):
        """标记邮件为已读"""
        try:
            mail.store(email_id, '+FLAGS', '\\Seen')
            return True
        except Exception:
            return False
    
    def process_unread_emails(self):
        """处理所有未读邮件"""
        mail = self.get_email_connection()
        if not mail:
            return False, 0, 0
        
        try:
            # 搜索未读邮件
            status, messages = mail.search(None, 'UNSEEN')
            if status != "OK" or not messages[0]:
                return True, 0, 0
            
            email_ids = messages[0].split()
            total_count = len(email_ids)
            processed_count = 0
            forwarded_count = 0
            
            logger.info(f"📨 发现 {total_count} 封未读邮件")
            
            # 处理每封邮件
            for email_id in email_ids:
                email_data = self.fetch_email_content(mail, email_id)
                if not email_data:
                    continue
                
                # 判断是否处理
                should_process, verification_code = self.should_process_email(
                    email_data['subject'], 
                    email_data['body']
                )
                
                if should_process and verification_code:
                    # 提取有效期信息
                    validity_info = self.extract_validity_info(
                        email_data['subject'], 
                        email_data['body']
                    )
                    
                    # 发送到Telegram
                    self.send_to_telegram(
                        email_data['subject'],
                        verification_code,
                        email_data['time'],
                        validity_info
                    )
                    forwarded_count += 1
                    logger.info(f"✅ 转发: {email_data['subject'][:60]}...")
                else:
                    logger.debug(f"⏭️  跳过: {email_data['subject'][:50]}...")
                
                # 标记为已读（无论是否转发）
                self.mark_email_as_read(mail, email_id)
                processed_count += 1
            
            if forwarded_count > 0:
                logger.info(f"📊 本次转发 {forwarded_count} 封验证码邮件")
            
            return True, processed_count, forwarded_count
            
        except Exception as e:
            logger.error(f"❌ 处理未读邮件时出错: {e}")
            return False, 0, 0
        finally:
            # 确保关闭连接
            try:
                mail.close()
                mail.logout()
            except:
                pass
    
    def run(self):
        """主监控循环"""
        logger.info(f"🚀 邮箱监控服务启动")
        
        check_interval = 15  # 检查间隔（秒）
        heartbeat_counter = 0
        error_count = 0
        
        while True:
            try:
                heartbeat_counter += 1
                
                # 心跳日志（防WebSocket断开）
                if heartbeat_counter % 10 == 0:
                    logger.info(f"💓 服务运行中 | 检查次数: {heartbeat_counter} | {get_beijing_time_short()}")
                
                # 处理未读邮件
                success, processed, forwarded = self.process_unread_emails()
                
                if success:
                    error_count = max(0, error_count - 1)
                else:
                    error_count += 1
                    logger.warning(f"⚠️ 处理失败 ({error_count}/5)")
                
                # 错误过多时延长等待
                if error_count >= 5:
                    wait_time = 60
                    logger.error(f"❌ 连续错误过多，等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    error_count = 3
                    continue
                
                # 等待下次检查
                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                logger.info(f"👋 服务手动停止 | {get_beijing_time()}")
                break
            except Exception as e:
                logger.error(f"❌ 监控循环发生未预期错误: {e}")
                time.sleep(30)

# ========== 4. 主程序入口 ==========
def main():
    """程序主入口"""
    
    # 启动健康检查服务器（防休眠）
    health_thread = threading.Thread(target=health_server, daemon=True)
    health_thread.start()
    logger.info("✅ 健康检查服务器已启动（端口 8000）")
    
    # 启动邮箱监控
    try:
        monitor = EmailMonitor()
        monitor.run()
    except ValueError as e:
        logger.error(f"❌ 配置错误: {e}")
        logger.error("💡 请检查Koyeb环境变量: EMAIL, PASSWORD, BOT_TOKEN, CHAT_ID")
        time.sleep(30)
    except Exception as e:
        logger.error(f"❌ 服务启动失败: {e}")
        time.sleep(30)

if __name__ == "__main__":
    main()
