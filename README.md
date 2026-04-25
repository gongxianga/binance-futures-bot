# 币安合约交易软件 v4.0 - 安全增强版 🔒

一款功能强大、安全可靠的币安合约自动化交易软件，支持多种交易策略和远程访问控制。

## ✨ 核心功能

### 📊 交易功能
- ✅ 支持币安合约交易（测试网/正式网）
- ✅ 手动交易（市价单/限价单）
- ✅ 自动策略扫描（30分钟一次）
- ✅ 混合策略：量价突破 + RSI + 动量 + 布林带
- ✅ 自动止损止盈设置
- ✅ 实时持仓监控
- ✅ 市场雷达图可视化
- ✅ 支持多空双向交易

### 🔐 安全特性（v4.0 新增）

#### 1. 用户认证系统
- 用户名/密码登录
- 密码使用 Werkzeug 加密存储
- Flask-Login 会话管理
- 自动登录状态检查

#### 2. 验证码防护
- 登录时需输入图形验证码
- 验证码自动生成，包含数字
- 5分钟自动过期
- 点击刷新验证码
- 有效防止自动化暴力破解

#### 3. 登录失败限制
- 同一IP连续失败5次后锁定
- 锁定时间：5分钟（300秒）
- 实时显示剩余锁定时间
- 防止密码暴力破解攻击

#### 4. API密钥加密存储
- 使用 Fernet 对称加密算法
- API Key 和 Secret 加密后保存
- 密钥文件独立存储（secret.key）
- 前端只显示部分密钥（脱敏处理）
- 即使配置文件泄露也无法获取真实密钥

#### 5. IP白名单访问控制
- 支持配置允许访问的IP地址列表
- 可限制特定设备登录
- 白名单为空时允许所有IP（灵活配置）
- 每次登录验证IP合法性

#### 6. 会话安全
- 随机生成 Secret Key
- 每个会话独立验证
- 支持安全登出
- 防止会话劫持

## 🚀 快速开始

### 方式1：一键安装（推荐）

**Linux / macOS:**
```bash
git clone https://github.com/gongxianga/binance-futures-bot.git
cd binance-futures-bot
chmod +x install.sh
./install.sh
python3 app.py
```

**或者使用自动安装脚本：**
```bash
curl -sSL https://raw.githubusercontent.com/gongxianga/binance-futures-bot/main/install.sh | bash
```

### 方式2：手动安装

**1. 克隆仓库**
```bash
git clone https://github.com/gongxianga/binance-futures-bot.git
cd binance-futures-bot
```

**2. 安装依赖**
```bash
pip install -r requirements.txt
```

主要依赖：
- `python-binance` - 币安API交互
- `flask` - Web框架
- `flask-login` - 登录会话管理
- `cryptography` - API密钥加密
- `pillow` - 验证码图片生成
- `matplotlib` - 数据可视化
- `numpy` - 数值计算

**3. 启动程序**
```bash
python app.py
```

**4. 访问界面**

浏览器打开：`http://localhost:5000`

**5. 登录**

**默认账户：**
- 用户名：`admin`
- 密码：`admin123`

⚠️ **重要：首次登录后请立即修改密码！**

**6. 配置币安API**

登录成功后：
1. 在顶部输入币安 API Key 和 Secret
2. 选择测试网或正式网
3. 点击"连接"按钮

## 📁 项目结构

```
binance_futures_bot/
├── app.py                  # 主程序（已集成安全认证）
├── auth.py                 # 认证与安全模块
├── requirements.txt        # Python依赖
├── config.json            # 币安API配置（加密）
├── users.json             # 用户账户配置
├── secret.key             # 加密密钥（请妥善保管）
├── templates/
│   ├── index.html         # 主界面
│   └── login.html         # 登录页面
├── 安全功能说明.txt        # 详细安全说明
├── 启动.bat               # Windows启动脚本
├── test_security.py       # 安全功能测试脚本
└── README.md              # 本文件
```

## 🔧 高级配置

### 修改密码

方法1：编辑 `users.json`，使用加密后的密码哈希

```python
from werkzeug.security import generate_password_hash
print(generate_password_hash('你的新密码'))
```

方法2：删除 `users.json` 文件，重启程序会重新创建默认账户

### 配置IP白名单

编辑 `users.json` 文件：

```json
{
  "1": {
    "username": "admin",
    "password_hash": "pbkdf2:sha256:...",
    "ip_whitelist": ["192.168.1.100", "10.0.0.5"]
  }
}
```

留空表示允许所有IP：
```json
"ip_whitelist": []
```

### 添加新用户

在 `users.json` 中添加新条目：

```json
{
  "2": {
    "username": "trader2",
    "password_hash": "pbkdf2:sha256:...",
    "ip_whitelist": []
  }
}
```

### 调整登录限制

编辑 `app.py` 中的参数：

```python
rate_limiter = LoginRateLimiter(
    max_attempts=5,      # 最大尝试次数
    lockout_duration=300 # 锁定时长（秒）
)
```

## 🌐 远程访问

程序默认监听 `0.0.0.0:5000`，支持远程访问。

### 配置步骤：

1. **开放防火墙端口**
   ```bash
   # Linux
   sudo ufw allow 5000

   # Windows
   # 在防火墙设置中添加入站规则
   ```

2. **配置IP白名单**（推荐）
   限制只有特定IP可以访问

3. **使用HTTPS**（生产环境）
   建议使用 Nginx 反向代理配合SSL证书

4. **访问地址**
   ```
   http://服务器IP:5000
   ```

## 🎯 交易策略说明

### 策略扫描器

程序集成了4种技术指标的混合策略：

1. **量价突破** - 成交量大于平均值2.5倍时触发
2. **RSI指标** - RSI < 30 做多，RSI > 70 做空
3. **动量排名** - 涨跌幅和成交量综合排名
4. **布林带** - 突破上轨做多，跌破下轨做空

### 信号评分

- 每个指标触发得1分
- 最高4分，最低0分
- 可设置最低信号分数过滤

### 执行模式

- **手动确认**：扫描到信号后需要手动确认交易
- **全自动**：满足条件自动下单（需谨慎使用）

### 风控设置

- 每笔交易金额（USDT）
- 杠杆倍数（1-125倍）
- 止损百分比
- 止盈百分比
- 扫描频率（30分钟）

## ⚠️ 安全建议

1. ✅ **立即修改默认密码**
2. ✅ **配置IP白名单**（如果固定设备访问）
3. ✅ **定期备份** `users.json` 和 `secret.key`
4. ✅ **不要分享** `secret.key` 文件
5. ✅ **生产环境使用HTTPS**
6. ✅ **定期检查日志**中的可疑登录
7. ✅ **测试网充分测试**后再使用正式网
8. ✅ **合理设置杠杆**，控制风险

## 🐛 故障排除

### Q: 忘记密码怎么办？
**A:** 删除 `users.json` 文件，重启程序会重新创建默认账户（admin/admin123）

### Q: 被锁定怎么办？
**A:** 等待5分钟后自动解锁，或重启程序立即解锁

### Q: 验证码显示不出来？
**A:** 检查是否安装 Pillow 库：`pip install pillow`

### Q: IP白名单不生效？
**A:**
- 检查 `users.json` 格式是否正确
- 确认IP地址准确（可在日志中查看实际IP）
- 注意代理和NAT可能改变真实IP

### Q: API连接失败？
**A:**
- 检查API Key和Secret是否正确
- 确认是否选择了正确的网络（测试网/正式网）
- 测试网地址：https://testnet.binancefuture.com
- 正式网地址：https://fapi.binance.com

### Q: 如何查看系统日志？
**A:** 程序运行时在界面底部有实时日志显示

## 🔬 测试

运行安全功能测试：

```bash
python test_security.py
```

这将测试：
- 模块导入
- 用户管理器
- 登录限制器
- 验证码生成器
- API密钥加密

## 📊 性能说明

- **扫描频率**：30分钟一次（可调）
- **扫描范围**：成交量前30的交易对
- **实时更新**：持仓、账户每5秒刷新
- **日志保存**：最近200条
- **会话超时**：根据Flask默认配置

## 📜 更新日志

### v4.0 - 2025-04-25（当前版本）
- ✨ 新增完整的用户认证系统
- 🔐 集成验证码登录防护
- 🛡️ 添加登录失败次数限制
- 🔒 API密钥加密存储
- 🌐 支持IP白名单访问控制
- 🚀 优化远程访问安全性
- 📝 完善安全文档

### v3.0
- 添加混合策略扫描
- 优化雷达图显示
- 支持自动/手动交易模式

### v2.0
- 改为Web界面
- 集成ECharts图表
- 添加实时日志

### v1.0
- 基础交易功能
- 桌面GUI界面

## 📞 支持

- 详细说明：查看 `安全功能说明.txt`
- 测试脚本：运行 `test_security.py`
- 在线文档：本 README.md

## ⚖️ 免责声明

本软件仅供学习研究使用。加密货币交易具有高风险，请：

- 充分理解交易风险
- 先在测试网测试
- 合理控制仓位
- 设置止损止盈
- 自行承担交易损失

**作者不对使用本软件造成的任何损失负责。**

## 📄 许可

MIT License

---

**如有问题，请检查日志或参考安全功能说明文档。**

祝交易顺利！🚀
