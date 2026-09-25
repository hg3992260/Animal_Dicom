#!/usr/bin/env bash
# macOS .app 自洽性硬门禁 —— 防止再发出"启动即崩"的 DMG。
#
# 两次崩溃的检查点（2026-09-17 复盘，详见 ssd_vr_viewer_macos.spec 顶部）:
#   ① SIGABRT: 裸命名平台插件 libqcocoa.dylib 的 Qt 依赖没进包（依赖闭合，第 4 节）
#   ② EXC_BAD_ACCESS: 包里有两/三套 Qt（conda 裸 dylib + pip wheel 版本化 dylib +
#      pip wheel framework）→ ObjC 类重复注册 → 事件循环随机崩（第 3 节 + 第 5 节
#      的 "is implemented in both" 检测）
#
# 用法:
#   bash tools/verify_macos_bundle.sh dist/SSD_VR_Fusion_Viewer.app
# 可选环境变量:
#   SKIP_SMOKE=1       跳过启动冒烟（只做静态校验）
#   SMOKE_TIMEOUT=45   冒烟最长观察秒数（等到 MCP bridge 行即可提前结束）
#   ALLOW_WHEEL_QT=1   允许 pip wheel 版 Qt（仅本地实验；CI 不要开）
set -u

APP="${1:-dist/SSD_VR_Fusion_Viewer.app}"
EXE_NAME="SSD_VR_Fusion_Viewer"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-45}"
FAILURES=0

ok()   { printf '  [ok]   %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
warn() { printf '  [warn] %s\n' "$1"; }
info() { printf '  [info] %s\n' "$1"; }
head() { printf '\n== %s ==\n' "$1"; }

head "1. bundle 结构"
if [ ! -d "$APP" ]; then
  printf '找不到 app bundle: %s\n' "$APP"
  exit 2
fi
APP_EXE="$APP/Contents/MacOS/$EXE_NAME"
APP_RES="$APP/Contents/Resources"
[ -x "$APP_EXE" ] && ok "可执行文件: $APP_EXE" || bad "找不到可执行文件 $APP_EXE"
[ -d "$APP_RES" ] && ok "Resources 目录存在" || bad "缺少 Contents/Resources"

head "2. Qt 平台插件（cocoa）"
COCOA=$(find "$APP" -name 'libqcocoa.dylib' -type f 2>/dev/null | head -n 1)
if [ -n "$COCOA" ]; then
  ok "libqcocoa.dylib: ${COCOA#"$APP"/}"
else
  bad "包里没有 libqcocoa.dylib —— Qt 无法初始化 cocoa 平台，必崩"
fi
COCOA_COUNT=$(find "$APP" -name 'libqcocoa.dylib' -type f 2>/dev/null | wc -l | tr -d ' ')
PLATFORM_COUNT=$(find "$APP" -path '*plugins/platforms/*.dylib' -type f 2>/dev/null | wc -l | tr -d ' ')
info "platforms/ 下插件数: $PLATFORM_COUNT"
if [ "$COCOA_COUNT" -gt 1 ]; then
  bad "libqcocoa.dylib 在包里出现 $COCOA_COUNT 次（重复的平台插件同样会导致重复注册）"
  find "$APP" -name 'libqcocoa.dylib' -type f 2>/dev/null | sed 's/^/         /'
fi

head "3. Qt 只能有一套（conda 裸 dylib / pip wheel）"
# conda 风格：libQt6*.dylib 不在 /PySide6/Qt/ 下（通常落在 Contents/Frameworks 根）
NAKED_ROOT=$(find "$APP" -name 'libQt6*.dylib' -type f -not -path '*/PySide6/Qt/*' 2>/dev/null | wc -l | tr -d ' ')
WHEEL_LIBS=$(find "$APP" -path '*/PySide6/Qt/lib/*' -name 'libQt6*.dylib' -type f 2>/dev/null | wc -l | tr -d ' ')
WHEEL_FW=$(find "$APP" -path '*/PySide6/Qt/lib/*' -name 'Qt*.framework' -type d 2>/dev/null | wc -l | tr -d ' ')
# 兜底：任何位置的 Qt framework（pip wheel 的确定特征：conda-forge 的 pyside6
# 实测 framework 数为 0，所以"有 framework"就等于混进了 wheel 版 Qt）
ANY_FW=$(find "$APP" -name 'Qt*.framework' -type d 2>/dev/null | wc -l | tr -d ' ')
info "conda 风格 libQt6*.dylib : $NAKED_ROOT"
info "PySide6/Qt/lib 下 dylib  : $WHEEL_LIBS"
info "PySide6/Qt/lib 下 fw     : $WHEEL_FW"
info "任意位置 Qt*.framework   : $ANY_FW"

if [ "$NAKED_ROOT" -eq 0 ]; then
  bad "找不到 Qt 库（打包肯定不完整）"
elif [ "$ANY_FW" -gt 0 ] && [ "${ALLOW_WHEEL_QT:-0}" != "1" ]; then
  bad "包里存在 Qt*.framework（$ANY_FW 个）——这是 pip wheel 版 Qt 的特征，"
  bad "  与 conda 的裸 dylib Qt 混装会导致 ObjC 类重复注册（第二次崩溃的原因）"
  find "$APP" -name 'Qt*.framework' -type d 2>/dev/null | head -5 | sed 's/^/         /'
  bad "  修法见 .github/workflows/macos.yml：conda 装 vtk + pyside6，"
  bad "  PyCt6 用 pip install --no-deps，绝不要再 pip install PySide6"
else
  ok "只有一套 Qt（裸 dylib $NAKED_ROOT 个，无 framework）"
fi

# 同名 Qt 库出现在多处：内容不同 = 两套 Qt（致命）；内容相同 = 同一份被复制（提示）
DUPES=$(find "$APP" -name 'libQt6*.dylib' -type f 2>/dev/null -exec basename {} \; | sort | uniq -d)
if [ -n "$DUPES" ]; then
  DUP_TMP=$(mktemp)
  printf '%s\n' "$DUPES" > "$DUP_TMP"
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    HASHES=$(find "$APP" -name "$name" -type f -exec shasum -a 256 {} \; 2>/dev/null | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
    if [ "$HASHES" -gt 1 ]; then
      bad "同名但内容不同的 Qt 库 $name（$HASHES 份）—— 两套 Qt 混在一起，必崩"
      find "$APP" -name "$name" -type f 2>/dev/null | sed 's/^/         /'
    else
      warn "同名同内容的 Qt 库 $name 出现多次（同一份被复制到多处，通常无害）"
      find "$APP" -name "$name" -type f 2>/dev/null | sed 's/^/         /'
    fi
  done < "$DUP_TMP"
  rm -f "$DUP_TMP"
else
  ok "没有同名重复的 Qt 库"
fi

head "4. Qt 库依赖闭合（对照消费方的 otool -L）"
DEPS_TMP=$(mktemp)
CONSUMERS=$(find "$APP" \( -name 'libqcocoa.dylib' -o -name 'libvtkRenderingQt*.dylib' \) -type f 2>/dev/null)
if [ -z "$CONSUMERS" ]; then
  bad "找不到消费方（libqcocoa / libvtkRenderingQt），无法校验依赖闭合"
else
  for consumer in $CONSUMERS; do
    base=$(basename "$consumer")
    otool -L "$consumer" 2>/dev/null | tail -n +2 | awk '{print $1}' \
      | grep -E 'libQt6[A-Za-z0-9]*\.dylib$|Qt6[A-Za-z0-9]*\.framework' | sort -u > "$DEPS_TMP" || true
    if [ ! -s "$DEPS_TMP" ]; then
      info "$base: 没有 Qt 依赖（可能是静态链接？）"
      continue
    fi
    while IFS= read -r dep; do
      [ -n "$dep" ] || continue
      name=$(basename "$dep")
      if find "$APP" -name "$name" 2>/dev/null | grep -q .; then
        ok "$base -> $name"
      else
        bad "$base 需要 $name，但包里找不到（这正是第一次崩溃 SIGABRT 的原因）"
      fi
    done < "$DEPS_TMP"
  done
fi
rm -f "$DEPS_TMP"

if [ -f "$APP_RES/qt.conf" ]; then
  info "Contents/Resources/qt.conf 存在（可选）"
  sed 's/^/         /' "$APP_RES/qt.conf"
fi

head "5. 启动冒烟（QT_DEBUG_PLUGINS=1；等到 MCP bridge 行即通过）"
if [ "${SKIP_SMOKE:-0}" = "1" ]; then
  info "SKIP_SMOKE=1，跳过"
elif [ ! -x "$APP_EXE" ]; then
  bad "没有可执行文件，跳过冒烟"
else
  SMOKE_LOG=$(mktemp)
  # 不带 --no-mcp：桥默认开启，正好顺带验证 MCP 控制口能不能起来
  QT_DEBUG_PLUGINS=1 "$APP_EXE" > "$SMOKE_LOG" 2>&1 &
  SMOKE_PID=$!
  BRIDGE_SEEN=0
  elapsed=0
  while [ "$elapsed" -lt "$SMOKE_TIMEOUT" ]; do
    sleep 1
    elapsed=$((elapsed + 1))
    if grep -q '\[MCP bridge\] listening' "$SMOKE_LOG" 2>/dev/null; then
      BRIDGE_SEEN=1
      break
    fi
    kill -0 "$SMOKE_PID" 2>/dev/null || break
  done

  # 重复 ObjC 类：Qt 装了两遍的铁证
  if grep -q 'is implemented in both' "$SMOKE_LOG" 2>/dev/null; then
    bad "启动日志出现 'is implemented in both'（Qt 被装了两遍 → ObjC 类重复注册）"
    grep -m 4 'is implemented in both' "$SMOKE_LOG" | sed 's/^/         /'
  else
    ok "没有重复 ObjC 类告警"
  fi
  # Qt 平台插件致命错误
  for pat in 'Could not load the Qt platform plugin' \
             'no Qt platform plugin could be initialized' \
             'abort() called' 'SIGABRT'; do
    if grep -q "$pat" "$SMOKE_LOG" 2>/dev/null; then
      bad "启动日志命中致命字样: $pat"
    fi
  done

  if [ "$BRIDGE_SEEN" = "1" ]; then
    ok "已进入事件循环并起桥（[MCP bridge] listening，${elapsed}s）"
    kill "$SMOKE_PID" 2>/dev/null || true
    wait "$SMOKE_PID" 2>/dev/null || true
  elif kill -0 "$SMOKE_PID" 2>/dev/null; then
    warn "进程存活 ${SMOKE_TIMEOUT}s，但没看到 [MCP bridge] listening（桥没起来，检查 mcp_ssd_vr 是否入包）"
    kill "$SMOKE_PID" 2>/dev/null || true
    wait "$SMOKE_PID" 2>/dev/null || true
  else
    bad "进程在 ${elapsed}s 内退出且未起桥"
  fi

  printf '  ---- 启动日志（尾部 40 行）----\n'
  tail -n 40 "$SMOKE_LOG" | sed 's/^/  | /'
  rm -f "$SMOKE_LOG"
fi

printf '\n'
if [ "$FAILURES" -gt 0 ]; then
  printf 'VERDICT: FAIL  (%d 项不合格) —— 这个 .app/DMG 不要发布\n' "$FAILURES"
  exit 1
fi
printf 'VERDICT: PASS  —— bundle 自洽（单一 Qt、依赖闭合、能起桥），可以打包 DMG\n'
exit 0
