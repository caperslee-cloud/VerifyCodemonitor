#!/usr/bin/env python3
"""
专用版：QQ企业邮箱 → Telegram 转发 (精准过滤与优化格式版)
功能：1. 精准筛选含验证码邮件 2. 优化Telegram通知格式 3. 内置健康检查 4. 北京时间支持
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
from datetime import datetime, timedelta
import pytz

# ========== 配置说明（在Koyeb环境变量中设置）==========
# 必需：
# 1. EMAIL: 你的完整企业邮箱地址
# 2. PASSWORD: 企业邮箱的客户端专用密码
# 3. BOT_TOKEN: 你的Telegram Bot Token
# 4. CHAT_ID: 你的Telegram Chat ID（支持多个，用逗号分隔）
# 可选：
# 5. KEYWORDS: 自定义过滤关键词，用逗号分隔（默认已内置）
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
    """获取当前北京时间（字符串格式）"""
    now_utc = datetime.utcnow()
    now_beijing = pytz.utc.localize(now_utc).astimezone(BEIJING_TZ)
    return now_beijing.strftime('%Y-%m-%d %H:%M:%S')

def get_beijing_time_for_display():
    """获取用于显示的北京时间（仅时:分:秒）"""
    now_utc = datetime.utcnow()
    now_beijing = pytz.utc.localize(now_utc).astimezone(BEIJING_TZ)
    return now_beijing.strftime('%H:%M:%S')

def parse_email_time(email_time_str):
    """解析邮件头时间并转换为北京时间字符串"""
    if not email_time_str:
        return get_beijing_time_for_display()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(email_time_str)
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        beijing_time = dt.astimezone(BEIJING_TZ)
        return beijing_time.strftime('%H:%M:%S')
    except Exception as e:
        logger.warning(f"解析邮件时间失败，使用当前时间: {e}")
        return get_beijing_time_for_display()

# ========== 2. 健康检查服务器 ==========
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        current_time = get_beijing_time()
        self.wfile.write(f'服务运行正常 | 北京时间: {current_time}'.encode())
    
    def log_message(self, format, *args):
        """静默访问日志"""
        pass

def health_server():
    """启动健康检查服务器（端口8000）"""
    server = HTTPServer(('0.0.0.0', 8000), HealthHandler)
    logger.info(f"✅ 健康检查服务器已启动 | 服务启动时间: {get_beijing_time()}")
    server.serve_forever()

# ========== 3. QQ企业邮箱监控核心 ==========
class QqExmailMonitor:
    def __init__(self):
        # 固定配置：QQ企业邮箱服务器
        self.imap_server = "imap.exmail.qq.com"
        self.imap_port = 993
        
        # 从环境变量读取账号信息
        self.email = os.environ.get("EMAIL", "").strip()
        self.password = os.environ.get("PASSWORD", "").strip()
        self.bot_token = os.environ.get("BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("CHAT_ID", "").strip()
        
        # 内置精准过滤关键词（验证码相关）
        default_keywords = [
            "验证码", "校验码", "动态码", "安全码", "验证代码", 
            "code", "Code", "CODE", "verification", "Verification Code",
            "登入码", "登录码", "确认码", "激活码", "验证口令"
        ]
        
        # 读取用户自定义关键词（可选）
        keywords_str = os.environ.get("KEYWORDS", "").strip()
        if keywords_str:
            user_keywords = [kw.strip() for kw in keywords_str.split(",") if kw.strip()]
            self.keywords = list(set(default_keywords + user_keywords))  # 合并并去重
        else:
            self.keywords = default_keywords
        
        # 内置排除关键词（常见非验证码邮件）
        self.exclude_keywords = [
            "日报", "周报", "月报", "报告", "报表",
            "会议", "通知", "公告", "通讯", "简报",
            "账单", "发票", "收据", "订阅", "新闻稿",
            "欢迎", "注册成功", "激活成功", "密码修改"
        ]
        
        logger.info(f"🔍 过滤关键词: {', '.join(self.keywords[:8])}...")
        logger.info(f"🚫 排除关键词: {', '.join(self.exclude_keywords[:8])}...")
        
        # 检查必需配置
        if not all([self.email, self.password, self.bot_token, self.chat_id]):
            logger.error("❌ 错误：请设置所有必需环境变量 (EMAIL, PASSWORD, BOT_TOKEN, CHAT_ID)")
            raise ValueError("缺少必要配置")
        
        logger.info("=" * 60)
        logger.info(f"📧 监控邮箱: {self.email}")
        logger.info(f"🔐 服务器: {self.imap_server}")
        logger.info(f"⏰ 系统时区: 亚洲/上海 (UTC+8)")
        logger.info(f"🕛 当前北京时间: {get_beijing_time()}")
        logger.info("=" * 60)
    
    def should_forward_email(self, subject, body):
        """
        精准判断是否转发邮件
        返回: (should_forward, reason, verification_code)
        """
        combined_text = (subject + " " + body[:500]).lower()
        subject_lower = subject.lower()
        
        # 检查1：是否在排除名单中（优先排除）
        for exclude_word in self.exclude_keywords:
            if exclude_word in subject:
                return False, f"标题含排除词: '{exclude_word}'", None
        
        # 检查2：是否包含验证码关键词
        keyword_match = None
        for keyword in self.keywords:
            if keyword.lower() in combined_text:
                keyword_match = keyword
                break
        
        # 检查3：提取验证码（支持多种格式）
        verification_code = self.extract_verification_code(body)
        
        # 决策逻辑
        if verification_code:
            # 有验证码 -> 转发
            reason = f"检测到验证码: {verification_code}"
            if keyword_match:
                reason += f" | 匹配关键词: '{keyword_match}'"
            return True, reason, verification_code
        elif keyword_match:
            # 有关键词但无验证码 -> 记录但不转发（可能是验证码相关通知）
            return False, f"仅匹配关键词: '{keyword_match}' (未找到验证码)", None
        else:
            # 无关键词无验证码 -> 不转发
            return False, "未匹配任何关键词且未找到验证码", None
    
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
                logger.error("  可能原因: 1.密码错误 2.未使用客户端专用密码 3.IMAP服务未开启")
            return None
        except Exception as e:
            logger.error(f"❌ 连接邮箱失败: {e}")
            return None
    
    def get_unread_emails(self, mail):
        """获取所有未读邮件"""
        try:
            status, messages = mail.search(None, 'UNSEEN')
            if status != "OK" or not messages[0]:
                return []
            return messages[0].split()
        except Exception as e:
            logger.error(f"❌ 搜索未读邮件失败: {e}")
            return []
    
    def process_email(self, mail, email_id):
        """处理单封邮件"""
        try:
            # 获取邮件内容
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != "OK":
                return None
            
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # 提取标题
            subject_raw = msg.get("Subject", "无标题")
            decoded = decode_header(subject_raw)
            subject = ""
            for content, charset in decoded:
                if isinstance(content, bytes):
                    subject += content.decode(charset if charset else 'utf-8', errors='ignore')
                else:
                    subject += str(content)
            subject = subject.strip()
            
            # 提取邮件时间
            email_date = msg.get("Date", "")
            email_time_str = parse_email_time(email_date)
            
            # 提取正文
            body = self.extract_email_body(msg)
            
            # 精准判断是否转发
            should_forward, reason, verification_code = self.should_forward_email(subject, body)
            
            return {
                'id': email_id,
                'subject': subject,
                'body': body,
                'time': email_time_str,
                'should_forward': should_forward,
                'reason': reason,
                'verification_code': verification_code
            }
            
        except Exception as e:
            logger.error(f"❌ 处理邮件 {email_id} 失败: {e}")
            return None
    
    def extract_email_body(self, msg):
        """提取邮件正文（纯文本）"""
        body = ""
        try:
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/plain":
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode('utf-8', errors='ignore')
                                break
                        except:
                            continue
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode('utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"提取邮件正文失败: {e}")
        return body
    
    def extract_verification_code(self, text):
        """精准提取验证码（支持多种格式）"""
        if not text:
            return None
        
        # 验证码匹配模式（按优先级排序）
        patterns = [
            r'验证码[：:]\s*(\d{4,8})',          # 验证码：123456
            r'【.*?】\s*(\d{4,8})',              # 【支付宝】123456
            r'code[：:]\s*(\d{4,8})',            # code: 123456
            r'verification code[：:]\s*(\d{4,8})', # verification code: 123456
            r'校验码[：:]\s*(\d{4,8})',          # 校验码：123456
            r'动态码[：:]\s*(\d{4,8})',          # 动态码：123456
            r'\b(\d{6})\b',                     # 独立的6位数字
            r'\b(\d{4})\b',                     # 独立的4位数字
            r'(\d{4,8})[^\d]{0,5}有效',          # 123456有效
            r'[\[\(](\d{4,8})[\]\)]',           # [123456] 或 (123456)
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text[:800], re.IGNORECASE)
            for match in matches:
                code = match if isinstance(match, str) else match[0]
                if code.isdigit() and 4 <= len(code) <= 8:
                    # 验证码通常不会是一串连续重复的数字
                    if len(set(code)) > 1 or len(code) <= 4:
                        return code
        return None
    
    def send_to_telegram(self, subject, verification_code, email_time, reason=""):
        """发送优化格式的消息到Telegram"""
        try:
            # 构建优化格式的消息
            message = f"📨 验证码通知\n"
            message += f"──────────────────\n"
            message += f"📌 标题：{subject[:80]}{'...' if len(subject) > 80 else ''}\n"
            message += f"🕒 时间：{email_time}\n"
            message += f"🔐 验证码：`{verification_code}`\n"
            
            # 提取有效期信息
            validity_info = self.extract_validity_info(subject)
            if validity_info:
                message += f"📋 备注：{validity_info}\n"
            
            message += f"──────────────────"
            
            # 发送到所有Chat ID（支持多个）
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
            
            logger.info(f"📤 发送完成: {success_count}/{len(chat_ids)} 成功 | 原因: {reason}")
            return success_count > 0
                
        except Exception as e:
            logger.error(f"❌ 发送到Telegram时出错: {e}")
            return False
    
    def extract_validity_info(self, subject):
        """从标题中提取有效期信息"""
        patterns = [
            r'(\d+[分分钟])内有效',
            r'有效期[为:]?(\d+[分分钟])',
            r'有效时间[为:]?(\d+[分分钟])',
            r'(\d+[小小时])内有效',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, subject)
            if match:
                time_unit = match.group(1)
                return f"请勿泄露，{time_unit}内有效"
        
        # 如果没有找到有效期，检查是否有其他安全提示
        if any(word in subject for word in ["请勿泄露", "请勿告知", "保密"]):
            return "请勿泄露"
        
        return None
    
    def mark_email_as_read(self, mail, email_id):
        """标记邮件为已读"""
        try:
            mail.store(email_id, '+FLAGS', '\\Seen')
            return True
        except Exception as e:
            logger.error(f"标记邮件已读失败: {e}")
            return False
    
    def run_monitor_cycle(self):
        """执行单次监控循环"""
        mail = self.get_email_connection()
        if not mail:
            return False
        
        try:
            # 获取所有未读邮件
            unread_ids = self.get_unread_emails(mail)
            if not unread_ids:
                logger.debug("📭 没有未读邮件")
                return True
            
            logger.info(f"📨 发现 {len(unread_ids)} 封未读邮件")
            processed_count = 0
            forwarded_count = 0
            
            # 处理每封邮件
            for email_id in unread_ids:
                result = self.process_email(mail, email_id)
                if not result:
                    continue
                
                if result['should_forward']:
                    # 转发验证码邮件
                    self.send_to_telegram(
                        result['subject'],
                        result['verification_code'],
                        result['time'],
                        result['reason']
                    )
                    forwarded_count += 1
                else:
                    # 记录但不转发
                    logger.info(f"⏭️  跳过邮件: {result['subject'][:50]}... | 原因: {result['reason']}")
                
                # 无论是否转发，都标记为已读避免重复处理
                self.mark_email_as_read(mail, email_id)
                processed_count += 1
            
            logger.info(f"✅ 循环完成: 处理 {processed_count} 封 | 转发 {forwarded_count} 封")
            return True
            
        finally:
            # 确保关闭连接
            try:
                mail.close()
                mail.logout()
            except:
                pass
    
    def run(self):
        """主监控循环"""
        logger.info(f"🚀 QQ企业邮箱监控服务启动")
        logger.info(f"⏰ 开始时间: {get_beijing_time()}")
        
        check_interval = 15  # 检查间隔（秒）
        error_count = 0
        
        while True:
            try:
                cycle_start = time.time()
                
                # 执行监控循环
                success = self.run_monitor_cycle()
                
                if success:
                    error_count = max(0, error_count - 1)
                else:
                    error_count += 1
                    logger.warning(f"⚠️ 监控循环失败 ({error_count}/5)")
                
                # 错误过多时延长等待
                if error_count >= 5:
                    wait_time = 60
                    logger.error(f"❌ 连续错误过多，等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    error_count = 3  # 重置为中等错误计数
                    continue
                
                # 计算等待时间
                cycle_time = time.time() - cycle_start
                sleep_time = max(5, check_interval - cycle_time)
                
                if cycle_time > 10:
                    logger.debug(f"⏱️  本次检查用时较长: {cycle_time:.1f}秒")
                
                time.sleep(sleep_time)
                
            except KeyboardInterrupt:
                logger.info(f"👋 服务手动停止 | 停止时间: {get_beijing_time()}")
                break
            except Exception as e:
                logger.error(f"❌ 监控循环发生未预期错误: {e}")
                time.sleep(30)

# ========== 4. 主程序入口 ==========
def main():
    """程序主入口"""
    
    # 启动健康检查服务器（独立线程）
    health_thread = threading.Thread(target=health_server, daemon=True)
    health_thread.start()
    logger.info("✅ 健康检查服务器已在后台启动（端口 8000）")
    
    # 启动邮箱监控
    try:
        monitor = QqExmailMonitor()
        monitor.run()
    except ValueError as e:
        logger.error(f"❌ 配置错误: {e}")
        logger.error("💡 请检查Koyeb环境变量设置")
        time.sleep(30)
    except Exception as e:
        logger.error(f"❌ 服务启动失败: {e}")
        time.sleep(30)

if __name__ == "__main__":
    main()
