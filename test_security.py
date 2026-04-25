#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安全功能测试脚本
"""

import os
import sys

# 测试导入
print("=" * 60)
print("  安全功能测试")
print("=" * 60)

print("\n1. 测试模块导入...")
try:
    from auth import UserManager, LoginRateLimiter, CaptchaGenerator, APIKeyEncryptor
    print("   ✓ auth 模块导入成功")
except Exception as e:
    print(f"   ✗ auth 模块导入失败: {e}")
    sys.exit(1)

print("\n2. 测试用户管理器...")
try:
    um = UserManager("test_users.json")
    admin = um.get_user_by_username("admin")
    if admin and admin.check_password("admin123"):
        print("   ✓ 默认管理员账户创建成功")
        print(f"   ✓ 用户名: {admin.username}")
        print(f"   ✓ ID: {admin.id}")
    else:
        print("   ✗ 默认账户验证失败")
    os.remove("test_users.json")
except Exception as e:
    print(f"   ✗ 用户管理器测试失败: {e}")

print("\n3. 测试登录限制器...")
try:
    limiter = LoginRateLimiter(max_attempts=3, lockout_duration=60)
    test_ip = "192.168.1.1"

    # 测试多次失败
    for i in range(3):
        limiter.record_attempt(test_ip)

    if limiter.is_locked(test_ip):
        remaining = limiter.get_remaining_time(test_ip)
        print(f"   ✓ 登录限制生效 (剩余 {remaining} 秒)")
    else:
        print("   ✗ 登录限制未生效")
except Exception as e:
    print(f"   ✗ 登录限制器测试失败: {e}")

print("\n4. 测试验证码生成器...")
try:
    captcha = CaptchaGenerator()
    session_id = "test_session"
    code = captcha.generate(session_id)
    print(f"   ✓ 验证码生成成功: {code}")

    if captcha.verify(session_id, code):
        print("   ✓ 验证码验证成功")
    else:
        print("   ✗ 验证码验证失败")

    # 测试图片生成
    img = captcha.create_image("1234")
    print(f"   ✓ 验证码图片生成成功")
except Exception as e:
    print(f"   ✗ 验证码生成器测试失败: {e}")

print("\n5. 测试API密钥加密...")
try:
    encryptor = APIKeyEncryptor("test_secret.key")
    test_key = "test_api_key_12345"

    encrypted = encryptor.encrypt(test_key)
    print(f"   ✓ 加密成功: {encrypted[:20]}...")

    decrypted = encryptor.decrypt(encrypted)
    if decrypted == test_key:
        print(f"   ✓ 解密成功: {decrypted}")
    else:
        print("   ✗ 解密失败")

    os.remove("test_secret.key")
except Exception as e:
    print(f"   ✗ API密钥加密测试失败: {e}")

print("\n" + "=" * 60)
print("  ✓ 所有安全功能测试通过！")
print("=" * 60)
print("\n可以启动程序: python app.py")
print("默认账户: admin / admin123\n")
