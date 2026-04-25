#!/bin/bash
# 币安合约交易软件 v4.0 - 启动脚本

set -e

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "❌ 错误: 虚拟环境不存在"
    echo "请先运行安装脚本: ./install.sh"
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  币安合约交易软件 v4.0 - 安全增强版"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🚀 正在启动服务器..."
echo "🌐 访问地址: http://localhost:5000"
echo "🔐 默认账户: admin / admin123"
echo ""
echo "💡 停止程序按 Ctrl+C"
echo ""

# 激活虚拟环境并启动
source venv/bin/activate
python app.py
