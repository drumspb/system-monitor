#!/bin/bash

# Загрузка конфигурации
CONFIG_FILE="/etc/system_monitor/system_monitor.conf"
if [ ! -f "$CONFIG_FILE" ]; then
echo "Config file not found: $CONFIG_FILE" >&2
exit 1
fi

# Парсинг конфига
get_config() {
awk -F ' = ' -v section="$1" -v key="$2" '
$0 ~ /^\[.*\]/ {
gsub(/\[|\]/, "", $0)
current_section = $0
}
current_section == section && $1 == key {
gsub(/\"/, "", $2)
print $2
}
' "$CONFIG_FILE"
}

# Инициализация переменных
LOG_DIR=$(get_config "main" "log_dir")
PID_FILE=$(get_config "main" "pid_file")
INTERFACES=$(get_config "network" "interfaces" | tr ',' ' ')
KERNEL_EXCLUDE=$(get_config "kernel" "exclude")
SERVICES_EXCLUDE=$(get_config "services" "exclude")

# Создаем директории
mkdir -p "$LOG_DIR" || { echo "Cannot create log directory"; exit 1; }
touch "$PID_FILE" || { echo "Cannot create pid file"; exit 1; }

# Функция логирования
log() {
local log_file="$LOG_DIR/$1.log"
local message="$2"
echo "$(date "+%b %d %H:%M:%S") $message" >> "$log_file"

# Простая ротация логов (1000 строк)
if [ $(wc -l < "$log_file") -gt 1000 ]; then
tail -n 500 "$log_file" > "${log_file}.tmp"
mv "${log_file}.tmp" "$log_file"
fi
}
# Мониторинг CPU и RAM
monitor_cpu_ram_temp() {
local interval=$(get_config "intervals" "cpu_ram")

# Определяем пути к температурным датчикам
local temp_paths=(
"/sys/class/thermal/thermal_zone0/temp"
"/sys/class/thermal/thermal_zone1/temp"
)

while true; do
# Загрузка CPU
cpu_usage=$(top -bn1 | grep "Cpu(s)" | awk '{print 100 - $8}')

# Использование RAM
ram_usage=$(free | awk '/Mem/{printf "%.1f%%", $3/$2*100}')

# Получаем температуры с обоих датчиков
local temps=()
for path in "${temp_paths[@]}"; do
if [ -f "$path" ]; then
temp=$(awk '{printf "%.1f", $1/1000}' "$path" 2>/dev/null)
[ -n "$temp" ] && temps+=("$temp")
fi
done

# Форматируем температурные данные
local temp_str="N/A"
if [ ${#temps[@]} -gt 0 ]; then
if [ ${#temps[@]} -eq 1 ]; then
temp_str="${temps[0]}°C"
else
temp_str="CPU:${temps[0]}°C SOC:${temps[1]}°C"
fi
fi

# Логирование
log "cpu_stats" "CPU:${cpu_usage}% RAM:${ram_usage} ${temp_str}"

# Проверка перегрева (по первому датчику)
if [ -n "${temps[0]}" ] && [ $(echo "${temps[0]} > 80" | bc -l) -eq 1 ]; then
log "alerts" "WARNING: High temperature detected: ${temps[0]}°C"
fi

sleep "$interval"
done
}

# Мониторинг сервисов
monitor_services() {
local interval=$(get_config "intervals" "services")

while true; do
# Получаем упавшие службы с очисткой спецсимволов
systemctl list-units --type=service --state=failed --plain --no-legend |
awk -v exclude="$SERVICES_EXCLUDE" '!($1 ~ exclude) {print $1}' |
while read -r service; do
# Получаем чистый статус без юникод-символов
local status=$(systemctl status "$service" --no-pager |
grep -i "Active:" |
sed 's/^[[:space:]]*Active: //;s/[^a-zA-Z0-9() -]//g')

# Получаем причину ошибки
local error_msg=$(systemctl status "$service" --no-pager |
grep -iE "error|failed|code" |
head -n 1 |
sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

# Логируем информацию
log "services" "Служба $service упала: $status (Причина: ${error_msg:-неизвестно})"

# Получаем зависимости в чистом виде
local dependencies=$(systemctl list-dependencies "$service" --plain --no-legend |
grep -v '^\s' |
tr '\n' ' ' |
sed 's/[^a-zA-Z0-9. -]//g')

[ -n "$dependencies" ] && log "service_deps" "Зависимости $service: $dependencies"
done

sleep "$interval"
done
}

# Мониторинг ядра
monitor_kernel() {
local interval=$(get_config "intervals" "kernel")
while true; do
dmesg --level=err --time-format iso |
grep -Ev "$KERNEL_EXCLUDE" |
awk '{
gsub(/[-:]/," ",$0);
$1="";
print strftime("%b %d %H:%M:%S", mktime($0)) " kernel:" $0
}' >> "$LOG_DIR/kernel.log"
sleep "$interval"
done
}

# Мониторинг сети
monitor_network() {
local interval=$(get_config "intervals" "network")
while true; do
for iface in $INTERFACES; do
# Получаем базовый статус интерфейса
if ip link show "$iface" | grep -q "state UP"; then
status="UP"
else
status="DOWN"
fi

# Получаем все IPv4 адреса (если есть)
ips=$(ip -4 -o addr show "$iface" 2>/dev/null | awk '{print $4}' | paste -sd ',')

# Формируем строку для лога
if [ -z "$ips" ]; then
log_msg="$iface: $status"
else
log_msg="$iface: $status ($ips)"
fi

# Логируем
log "network" "$log_msg"
done
sleep "$interval"
done
}

# Запуск мониторов
monitor_cpu_ram_temp &
echo $! > "$PID_FILE"
monitor_services &
echo $! >> "$PID_FILE"
monitor_kernel &
echo $! >> "$PID_FILE"
monitor_network &
echo $! >> "$PID_FILE"

# Ожидание завершения
wait
