#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# 检查 Python 是否安装
check_python() {
    if command -v python3 &>/dev/null; then
        return 0
    elif command -v python &>/dev/null; then
        return 0
    else
        echo -e "${RED}未找到 Python，请先安装 Python 3!${NC}"
        exit 1
    fi
}

# 菜单显示
show_menu() {
    clear
    echo -e "${CYAN}===================================${NC}"
    echo -e "${CYAN}     Web-Scrcpy 生产环境一键管理     ${NC}"
    echo -e "${CYAN}===================================${NC}"
    echo -e "${GREEN}1.${NC} 新机安装环境依赖 (pip install)"
    echo -e "${GREEN}2.${NC} 自定义配置运行 (密码/多机/帧率/码率)"
    echo -e "${GREEN}3.${NC} 推荐设置一键运行 (默认参数直接启动)"
    echo -e "${GREEN}4.${NC} 🛑 一键停止后台服务"
    echo -e "${GREEN}5.${NC} 📄 查看后台运行日志"
    echo -e "${GREEN}0.${NC} 退出"
    echo -e "${CYAN}===================================${NC}"
    echo -n "请选择操作 [0-5]: "
}

# 1. 安装依赖
install_deps() {
    echo -e "\n${YELLOW}开始安装依赖...${NC}"
    check_python
    if [ -f "requirements.txt" ]; then
        pip3 install -r requirements.txt || pip install -r requirements.txt
        echo -e "${GREEN}依赖安装完成！${NC}"
    else
        echo -e "${RED}未找到 requirements.txt 文件！尝试手动安装...${NC}"
        pip3 install flask flask-socketio simple-websocket || pip install flask flask-socketio simple-websocket
        echo -e "${GREEN}基础依赖安装完成！${NC}"
    fi
    echo -e "按任意键返回菜单..."
    read -n 1
}

# 2. 自定义运行
run_custom() {
    echo -e "\n${CYAN}--- 自定义运行配置 ---${NC}"
    
    # 密码设置
    read -p "是否需要设置密码？(y/n, 默认: y): " set_pwd
    set_pwd=${set_pwd:-y}
    PASSWORD=""
    if [[ "$set_pwd" =~ ^[Yy]$ ]]; then
        read -p "请输入访问密码 (默认: bikexin): " pwd_input
        PASSWORD=${pwd_input:-bikexin}
    fi

    # 多机设置
    read -p "是否支持多客户端同时访问 (多机运行)？(y/n, 默认: y): " set_multi
    set_multi=${set_multi:-y}
    MULTI_ARG=""
    if [[ "$set_multi" =~ ^[Yy]$ ]]; then
        MULTI_ARG="--multi"
    fi

    # 帧率设置
    read -p "请输入最大帧率 (默认: 60): " fps_input
    FPS=${fps_input:-60}

    # 码率/分辨率设置
    read -p "请输入采集最大边长/分辨率 (默认: 720): " size_input
    SIZE=${size_input:-720}

    read -p "请输入视频码率(bps) (默认: 4000000): " bitrate_input
    BITRATE=${bitrate_input:-4000000}

    # 运行命令构建
    CMD="python3 app.py --max_fps $FPS --max_size $SIZE --video_bit_rate $BITRATE $MULTI_ARG"
    if [ -n "$PASSWORD" ]; then
        CMD="$CMD --password $PASSWORD"
    fi

    # 是否后台运行
    read -p "是否需要后台运行 (防SSH断开)? (y/n, 默认: y): " run_bg
    run_bg=${run_bg:-y}

    if ! command -v python3 &>/dev/null; then
        CMD="${CMD/python3/python}"
    fi

    echo -e "\n${YELLOW}即将执行命令: ${NC}$CMD"
    
    if [[ "$run_bg" =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}服务正在后台启动，日志将输出到 scrcpy.log${NC}"
        nohup $CMD > scrcpy.log 2>&1 &
        echo -e "${GREEN}已在后台运行！PID: $!${NC}"
        echo -e "可通过菜单选项 [5] 查看日志，[4] 停止服务。"
        echo -e "按任意键返回菜单..."
        read -n 1
    else
        echo -e "${GREEN}服务正在前台启动... (按 Ctrl+C 停止)${NC}"
        eval "$CMD"
    fi
}

# 3. 推荐运行
run_recommended() {
    echo -e "\n${YELLOW}使用推荐配置启动... (多机模式, 2Mbps, 720p, 60fps)${NC}"
    # 推荐配置相当于: 开启 multi，其他参数默认
    CMD="python3 app.py --video_bit_rate 2000000 --max_size 720 --max_fps 60 --multi"
    
    if ! command -v python3 &>/dev/null; then
        CMD="python app.py --video_bit_rate 2000000 --max_size 720 --max_fps 60 --multi"
    fi
    
    read -p "是否需要后台运行 (防SSH断开)? (y/n, 默认: y): " run_bg
    run_bg=${run_bg:-y}

    echo -e "${GREEN}命令: ${NC}$CMD"
    
    if [[ "$run_bg" =~ ^[Yy]$ ]]; then
        echo -e "${GREEN}服务正在后台启动，日志将输出到 scrcpy.log${NC}"
        nohup $CMD > scrcpy.log 2>&1 &
        echo -e "${GREEN}已在后台运行！PID: $!${NC}"
        echo -e "可通过菜单选项 [5] 查看日志，[4] 停止服务。"
        echo -e "按任意键返回菜单..."
        read -n 1
    else
        echo -e "${GREEN}服务正在前台启动... (按 Ctrl+C 停止)${NC}"
        eval "$CMD"
    fi
}

# 4. 停止服务
stop_service() {
    echo -e "\n${YELLOW}正在停止 Web-Scrcpy 后台服务...${NC}"
    # 查找并杀掉带 app.py 且带 python 关键字的进程
    PIDS=$(ps aux | grep "[p]ython.*app.py" | awk '{print $2}')
    if [ -n "$PIDS" ]; then
        kill $PIDS
        echo -e "${GREEN}已结束进程: $PIDS${NC}"
    else
        echo -e "${RED}没有找到正在运行的 web-scrcpy 服务。${NC}"
    fi
    echo -e "按任意键返回菜单..."
    read -n 1
}

# 5. 查看日志
view_logs() {
    if [ -f "scrcpy.log" ]; then
        echo -e "\n${CYAN}--- 服务实时日志 (按 Ctrl+C 退出查看) ---${NC}"
        tail -f scrcpy.log
    else
        echo -e "\n${RED}未找到 scrcpy.log，服务可能还未在后台运行过！${NC}"
        echo -e "按任意键返回菜单..."
        read -n 1
    fi
}


# 主循环
while true; do
    show_menu
    read choice
    case $choice in
        1)
            install_deps
            ;;
        2)
            run_custom
            break
            ;;
        3)
            run_recommended
            ;;
        4)
            stop_service
            ;;
        5)
            view_logs
            ;;
        0)
            echo -e "${GREEN}已退出。${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}无效输入，请重新选择！${NC}"
            sleep 1
            ;;
    esac
done
