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
echo ""

# 检查并创建虚拟环境
VENV_DIR="venv"

if [ -d "$VENV_DIR" ]; then
    echo "✓ 虚拟环境已存在"
else
    echo "📦 正在创建虚拟环境..."
    python3 -m venv $VENV_DIR

    if [ $? -eq 0 ]; then
        echo "✓ 虚拟环境创建成功"
    else
        echo "❌ 虚拟环境创建失败"
        echo ""
        echo "💡 提示: 请先安装 python3-venv"
        echo "   Ubuntu/Debian: sudo apt install python3-venv"
        echo "   CentOS/RHEL:   sudo yum install python3-venv"
        exit 1
    fi
fi

echo ""

# 激活虚拟环境并安装依赖
echo "📦 正在安装依赖包..."
source $VENV_DIR/bin/activate

pip install --upgrade pip -q
pip install -r requirements.txt -q

if [ $? -eq 0 ]; then
    echo "✓ 依赖包安装成功"
else
    echo "❌ 依赖包安装失败"
    deactivate
    exit 1
fi

deactivate

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ 安装完成！"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🚀 启动程序："
echo "   source venv/bin/activate"
echo "   python app.py"
echo ""
echo "或者一键启动："
echo "   ./start.sh"
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
echo "💡 停止程序按 Ctrl+C"
echo ""
