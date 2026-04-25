#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
认证与安全模块
"""

import json
import os
import time
import random
import string
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
from flask_login import UserMixin
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


# ─────────────────────────────────────────────
#  用户类
# ─────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, user_id, username, password_hash, ip_whitelist=None):
        self.id = user_id
        self.username = username
        self.password_hash = password_hash
        self.ip_whitelist = ip_whitelist or []

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_ip_allowed(self, ip):
        # 如果白名单为空，允许所有IP
        if not self.ip_whitelist:
            return True
        return ip in self.ip_whitelist


# ─────────────────────────────────────────────
#  用户管理器
# ─────────────────────────────────────────────

class UserManager:
    def __init__(self, users_file='users.json'):
        self.users_file = users_file
        self.users = {}
        self._load_users()

    def _load_users(self):
        """加载用户配置"""
        if os.path.exists(self.users_file):
            with open(self.users_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for uid, info in data.items():
                    self.users[uid] = User(
                        user_id=uid,
                        username=info['username'],
                        password_hash=info['password_hash'],
                        ip_whitelist=info.get('ip_whitelist', [])
                    )
        else:
            # 创建默认管理员账户：admin / admin123
            default_user = User(
                user_id='1',
                username='admin',
                password_hash=generate_password_hash('admin123'),
                ip_whitelist=[]
            )
            self.users['1'] = default_user
            self._save_users()

    def _save_users(self):
        """保存用户配置"""
        data = {}
        for uid, user in self.users.items():
            data[uid] = {
                'username': user.username,
                'password_hash': user.password_hash,
                'ip_whitelist': user.ip_whitelist
            }
        with open(self.users_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_user(self, user_id):
        """根据ID获取用户"""
        return self.users.get(user_id)

    def get_user_by_username(self, username):
        """根据用户名获取用户"""
        for user in self.users.values():
            if user.username == username:
                return user
        return None

    def add_user(self, username, password, ip_whitelist=None):
        """添加新用户"""
        user_id = str(len(self.users) + 1)
        user = User(
            user_id=user_id,
            username=username,
            password_hash=generate_password_hash(password),
            ip_whitelist=ip_whitelist or []
        )
        self.users[user_id] = user
        self._save_users()
        return user

    def update_ip_whitelist(self, user_id, ip_list):
        """更新IP白名单"""
        user = self.get_user(user_id)
        if user:
            user.ip_whitelist = ip_list
            self._save_users()
            return True
        return False


# ─────────────────────────────────────────────
#  登录失败限制
# ─────────────────────────────────────────────

class LoginRateLimiter:
    def __init__(self, max_attempts=5, lockout_duration=300):
        """
        max_attempts: 最大尝试次数
        lockout_duration: 锁定时长（秒）
        """
        self.max_attempts = max_attempts
        self.lockout_duration = lockout_duration
        self.attempts = {}  # {ip: [timestamp1, timestamp2, ...]}
        self.locked = {}    # {ip: unlock_time}

    def is_locked(self, ip):
        """检查IP是否被锁定"""
        if ip in self.locked:
            if time.time() < self.locked[ip]:
                return True
            else:
                # 锁定时间已过，解锁
                del self.locked[ip]
                if ip in self.attempts:
                    del self.attempts[ip]
        return False

    def get_remaining_time(self, ip):
        """获取剩余锁定时间（秒）"""
        if ip in self.locked:
            remaining = int(self.locked[ip] - time.time())
            return max(0, remaining)
        return 0

    def record_attempt(self, ip):
        """记录登录尝试"""
        now = time.time()

        # 清理60秒前的记录
        if ip not in self.attempts:
            self.attempts[ip] = []
        self.attempts[ip] = [t for t in self.attempts[ip] if now - t < 60]

        # 添加本次尝试
        self.attempts[ip].append(now)

        # 检查是否超过限制
        if len(self.attempts[ip]) >= self.max_attempts:
            self.locked[ip] = now + self.lockout_duration
            return False

        return True

    def reset(self, ip):
        """重置IP的尝试记录"""
        if ip in self.attempts:
            del self.attempts[ip]
        if ip in self.locked:
            del self.locked[ip]


# ─────────────────────────────────────────────
#  验证码生成器
# ─────────────────────────────────────────────

class CaptchaGenerator:
    def __init__(self):
        self.captcha_store = {}  # {session_id: {'code': '1234', 'expire': timestamp}}

    def generate(self, session_id, length=4):
        """生成验证码"""
        code = ''.join(random.choices(string.digits, k=length))
        self.captcha_store[session_id] = {
            'code': code,
            'expire': time.time() + 300  # 5分钟过期
        }
        return code

    def verify(self, session_id, code):
        """验证验证码"""
        if session_id not in self.captcha_store:
            return False

        stored = self.captcha_store[session_id]

        # 检查是否过期
        if time.time() > stored['expire']:
            del self.captcha_store[session_id]
            return False

        # 验证码验证成功后删除
        if stored['code'] == code:
            del self.captcha_store[session_id]
            return True

        return False

    def create_image(self, code, width=120, height=40):
        """生成验证码图片"""
        # 创建图片
        image = Image.new('RGB', (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 绘制干扰线
        for _ in range(3):
            x1 = random.randint(0, width)
            y1 = random.randint(0, height)
            x2 = random.randint(0, width)
            y2 = random.randint(0, height)
            draw.line([(x1, y1), (x2, y2)], fill=(200, 200, 200), width=1)

        # 绘制验证码
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
        except:
            font = ImageFont.load_default()

        # 绘制每个字符
        for i, char in enumerate(code):
            x = 20 + i * 25
            y = random.randint(5, 10)
            color = (random.randint(0, 100), random.randint(0, 100), random.randint(0, 100))
            draw.text((x, y), char, fill=color, font=font)

        # 添加噪点
        for _ in range(100):
            x = random.randint(0, width - 1)
            y = random.randint(0, height - 1)
            draw.point((x, y), fill=(random.randint(150, 200), random.randint(150, 200), random.randint(150, 200)))

        # 转换为字节流
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer


# ─────────────────────────────────────────────
#  API密钥加密
# ─────────────────────────────────────────────

class APIKeyEncryptor:
    def __init__(self, key_file='secret.key'):
        self.key_file = key_file
        self.fernet = self._load_or_create_key()

    def _load_or_create_key(self):
        """加载或创建加密密钥"""
        if os.path.exists(self.key_file):
            with open(self.key_file, 'rb') as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
        return Fernet(key)

    def encrypt(self, text):
        """加密文本"""
        if not text:
            return ""
        return self.fernet.encrypt(text.encode()).decode()

    def decrypt(self, encrypted_text):
        """解密文本"""
        if not encrypted_text:
            return ""
        try:
            return self.fernet.decrypt(encrypted_text.encode()).decode()
        except Exception:
            return ""
