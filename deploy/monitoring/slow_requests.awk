# deploy/monitoring/slow_requests.awk — 从混合日志中提取耗时并统计慢请求（TASK-REL-003）
#
# 用法：
#   awk -v threshold=1000 -v records=/tmp/slow.tsv \
#       -f deploy/monitoring/slow_requests.awk LOGFILE
#
# 支持三种行格式（同一文件内可混排，docker compose logs 的多服务前缀不影响）：
#   1) api JSON 行：{"method":"GET","path":"/api/x","status":200,"latency_ms":1500,...}
#   2) nginx JSON 行：{"request":"GET /api/x HTTP/1.1","status":200,"request_time":1.5,...}
#      （request_time 单位为秒，这里换算成毫秒；也可用 latency_ms）
#   3) nginx 文本格式且带耗时的自定义 log_format：
#      172.18.0.1 - - [...] "GET /api/x HTTP/1.1" 504 0 "-" "curl/8" rt=2.300
#      （也接受 request_time=0.123 / latency_ms=1200）
#      默认 combined 格式没有耗时字段，只能计入 unknown（无法判定快慢），这是已知限制。
#
# 输出：
#   - 慢请求明细写入 -v records 指定的文件：latency_ms<TAB>path<TAB>method<TAB>status
#   - stdout 最后一行统计：STATS<TAB>total<TAB>access<TAB>parsed<TAB>slow<TAB>unknown<TAB>http5xx
#     total=扫描行数，access=识别为访问日志的行数，parsed=带耗时的行数，
#     slow=latency>threshold 的行数，unknown=访问日志但无耗时字段，http5xx=status>=500 的行数

function jstr(line, key,    re, s) {
    re = "\"" key "\"[ \t]*:[ \t]*\"[^\"]*\""
    if (match(line, re)) {
        s = substr(line, RSTART, RLENGTH)
        sub(/^[^:]*:[ \t]*"/, "", s)
        sub(/"$/, "", s)
        return s
    }
    return ""
}

function jnum(line, key,    re, s) {
    re = "\"" key "\"[ \t]*:[ \t]*[0-9]+(\\.[0-9]+)?"
    if (match(line, re)) {
        s = substr(line, RSTART, RLENGTH)
        sub(/^[^:]*:[ \t]*/, "", s)
        return s
    }
    return ""
}

function split_request(req,    n, parts) {
    n = split(req, parts, " ")
    if (n >= 2) {
        method = parts[1]
        path = parts[2]
    }
}

function parse_text_request(line,    req, rest) {
    if (!match(line, /"[A-Z]+ [^" \t]+ HTTP\/[0-9.]+"/)) {
        return
    }
    req = substr(line, RSTART + 1, RLENGTH - 2)
    split_request(req)
    rest = substr(line, RSTART + RLENGTH)
    if (match(rest, /[ \t][1-5][0-9][0-9]([ \t]|$)/)) {
        status = substr(rest, RSTART + 1, 3)
    }
}

function parse_text_latency(line,    s) {
    if (match(line, /(^|[ \t])rt=[0-9]+(\.[0-9]+)?/)) {
        s = substr(line, RSTART, RLENGTH)
        sub(/^.*rt=/, "", s)
        return s * 1000
    }
    if (match(line, /(^|[ \t])request_time=[0-9]+(\.[0-9]+)?/)) {
        s = substr(line, RSTART, RLENGTH)
        sub(/^.*request_time=/, "", s)
        return s * 1000
    }
    if (match(line, /(^|[ \t])latency_ms=[0-9]+(\.[0-9]+)?/)) {
        s = substr(line, RSTART, RLENGTH)
        sub(/^.*latency_ms=/, "", s)
        return s
    }
    return ""
}

{
    total++
    line = $0
    # 去掉 docker compose 可能保留的 ANSI 颜色码
    gsub(/\033\[[0-9;]*m/, "", line)

    lat = ""
    method = ""
    path = ""
    status = ""
    access = 0

    if (index(line, "\"latency_ms\"") > 0 || index(line, "\"latencyMs\"") > 0) {
        access = 1
        lat = jnum(line, "latency_ms")
        if (lat == "") {
            lat = jnum(line, "latencyMs")
        }
        method = jstr(line, "method")
        path = jstr(line, "path")
        status = jnum(line, "status")
        if (path == "" && index(line, "\"request\"") > 0) {
            req = jstr(line, "request")
            if (req != "") {
                split_request(req)
            }
        }
    } else if (index(line, "\"request\"") > 0) {
        access = 1
        req = jstr(line, "request")
        if (req != "") {
            split_request(req)
        }
        status = jnum(line, "status")
        rt = jnum(line, "request_time")
        if (rt != "") {
            lat = rt * 1000
        }
        if (lat == "") {
            lat = jnum(line, "latency_ms")
        }
    } else if (match(line, /"[A-Z]+ [^" \t]+ HTTP\/[0-9.]+"/)) {
        access = 1
        parse_text_request(line)
        lat = parse_text_latency(line)
    }

    if (!access) {
        next
    }
    access_count++

    if (lat == "") {
        unknown++
        next
    }

    sub(/\?.*$/, "", path)
    gsub(/\t/, " ", path)
    if (path == "") {
        path = "(unknown)"
    }
    if (method == "") {
        method = "-"
    }
    if (status == "") {
        status = "-"
    }

    parsed++
    if (status + 0 >= 500) {
        http5xx++
    }

    if (lat + 0 > threshold) {
        slow++
        if (records != "") {
            print lat "\t" path "\t" method "\t" status >> records
        }
    }
}

END {
    printf "STATS\t%d\t%d\t%d\t%d\t%d\t%d\n", total, access_count, parsed, slow, unknown, http5xx
}
