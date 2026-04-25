#!/bin/bash
# 币安合约交易软件 v4.0 - 一键安装脚本

set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  币安合约交易软件 v4.0 - 安全增强版"
echo "  一键安装脚本"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到 Python3，请先安装 Python 3.9+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "✓ Python 版本: $PYTHON_VERSION"

# 检查pip
if ! command -v pip3 &> /dev/null; then
    echo "❌ 错误: 未找到 pip3"
    exit 1
fi

echo "✓ pip 已安装"
echo ""

# 安装依赖
echo "📦 正在安装依赖包..."
pip3 install -r requirements.txt -q

if [ $? -eq 0 ]; then
    echo "✓ 依赖包安装成功"
else
    echo "❌ 依赖包安装失败"
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ 安装完成！"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🚀 启动程序："
echo "   python3 app.py"
echo ""
echo "🌐 然后访问："
echo "   http://localhost:5000"
echo ""
echo "🔐 默认账户："
echo "   用户名: admin"
echo "   密码:   admin123"
echo ""
echo "⚠️  首次登录后请立即修改密码！"
echo ""
