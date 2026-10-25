#!/bin/bash

function inspect_hours {
  cron_str=$1
  cron_hours=$(echo "$cron_str" | awk '{print $2}')
  echo "$cron_hours"
}

function inspect_next {
  cron_str=$1
  cron_str="${cron_str//\'/}"
  minute=$(TZ=UTC date '+%M')
  minute=$((10#$minute))
  hour=$(TZ=UTC date '+%H')
  hour=$((10#$hour))
  cron_minute=$(echo "$cron_str" | awk '{print $1}')
  cron_hours=$(echo "$cron_str" | awk '{print $2}')
  # echo "current $hour:$minute"
  # echo "cron hours: $cron_hours"
  next_exec_hour=$(echo "$cron_hours" | awk -v min="$minute" -v hour="$hour" -v cron_min="$cron_minute" -F ',' '{
    for (i=1;i<=NF;i++) {
      if ($i>hour || $i==hour && cron_min>min) {
         print $i
         break
      }
    }
  }')
  if test -z "$next_exec_hour"; then
    next_exec_hour=$(echo "$cron_hours" | awk -F ',' '{print $1}')
  fi
  echo "next exec time: UTC($next_exec_hour:$cron_minute) 北京时间($(((next_exec_hour + 8) % 24)):$cron_minute)"
}

function hours_except_now {
  cron_hours=$1
  hour=$(TZ=UTC date '+%H')
  hour=$((10#$hour))
  except_current_hours=$(echo "$cron_hours" | awk -v hour="$hour" -F ',' '{
    for (i=1;i<=NF;i++) {
      if ($i!=hour) {
        print $i
      }
    }
  }')
  result=""
  while IFS= read -r line; do
    if [ -z "$result" ]; then
      result="$line"
    else
      result="$result,$line"
    fi
  done <<< "$except_current_hours"
  if test -z "$result"; then
    # 只有一个小时，则直接返回当前值
    result=$cron_hours
  fi
  echo "$result"
}

function convert_utc_to_shanghai {
  local cron_str=$1
  echo "UTC时间: ${cron_str}"
  minute=$(echo "$cron_str" | awk '{print $1}')
  hours=$(echo "$cron_str" | awk '{print $2}')
  lines=$(echo "$hours"|awk -F ',' '{for (i=1;i<=NF;i++) { print ($i+8)%24 }}')
  # echo $lines
  result=""
  while IFS= read -r line; do
    if [ -z "$result" ]; then
      result="$line"
    else
      result="$result,$line"
    fi
  done <<< "$lines"
  echo "北京时间: $minute $result * * *'"
}

# 取出 run.yml 中当前生效的 cron 表达式，例如 '49 10,12 * * *'
# 只认「- cron: '...'」这一行，避免把 on:/schedule: 之类的内容带进来
function extract_current_cron {
  grep -E "^[[:space:]]*- cron: '" .github/workflows/run.yml | head -n 1 | sed -E "s/^[[:space:]]*- cron: //"
}

# 校验 CRON_HOURS 变量：只接受逗号分隔的 0-23，例如 0,2,4,6,8,14
# 这个变量是人工填的，很容易填成整段 YAML，必须挡住，否则会把 run.yml 写坏
function is_valid_cron_hours {
  local value="$1"
  local h
  local old_ifs="$IFS"
  if [[ ! "$value" =~ ^[0-9]{1,2}(,[0-9]{1,2})*$ ]]; then
    IFS="$old_ifs"
    return 1
  fi
  IFS=','
  for h in $value; do
    if (( 10#$h > 23 )); then
      IFS="$old_ifs"
      return 1
    fi
  done
  IFS="$old_ifs"
  return 0
}

function persist_execute_log {
  local event_name=$1
  local new_cron_hours=$2
  echo "trigger by: ${event_name}" > cron_change_time
  {
    echo "current system time:"
    TZ='UTC' date "+%y-%m-%d %H:%M:%S" | xargs -I {} echo "UTC: {}"
    TZ='Asia/Shanghai' date "+%y-%m-%d %H:%M:%S" | xargs -I {} echo "北京时间: {}"
  } >> cron_change_time
  current_cron=$(extract_current_cron)
  if test -z "$current_cron"; then
    echo "::error::在 .github/workflows/run.yml 里找不到 cron 表达式，放弃更新。"
    return 1
  fi
  {
    echo "current cron:"
    convert_utc_to_shanghai "$current_cron"
  } >> cron_change_time
  os=$(uname -s)
  sed_prefix=(sed -i)
  if [[ $os == "Darwin" ]]; then
    sed_prefix=(sed -i '')
  fi
  cron_hours=$(inspect_hours "$current_cron")
  if test -n "$new_cron_hours"; then
    if is_valid_cron_hours "$new_cron_hours"; then
      cron_hours=$(hours_except_now "$new_cron_hours")
    else
      echo "::warning::CRON_HOURS 变量格式不正确（应形如 0,2,4,6,8,14，逗号分隔的 0-23 小时），本次已忽略它，保持原有小时。收到的值: $(printf '%s' "$new_cron_hours" | tr '\n' ' ')"
    fi
  fi
  "${sed_prefix[@]}" -E "s/(- cron: ')[0-9]+( [^[:space:]]+ \* \* \*')/\1$((RANDOM % 59)) ${cron_hours} * * *'/g" .github/workflows/run.yml
  # 兜底校验：改完之后 cron 行必须仍然合法，否则回滚，绝不把坏掉的 YAML 推上去
  if ! grep -Eq "^[[:space:]]*- cron: '[0-9]{1,2} [0-9]{1,2}(,[0-9]{1,2})* \* \* \*'[[:space:]]*$" .github/workflows/run.yml; then
    echo "::error::更新后 run.yml 的 cron 表达式不合法，已回滚 .github/workflows/run.yml。请检查 CRON_HOURS 变量。"
    git checkout -- .github/workflows/run.yml
    return 1
  fi
  current_cron=$(extract_current_cron)
  {
    echo "next cron:"
    convert_utc_to_shanghai "$current_cron"
    inspect_next "$current_cron"
  } >> cron_change_time

}
