[English](README.md) | [简体中文](README.zh-CN.md)

# Macbee

**One sound. Your chosen Macs.** 通过局域网与 SSH 在多台 Apple Silicon Mac 之间同步共享**整机音频（Entire Mac）**——普通用户无需安装虚拟声卡驱动，无需单独安装 Snapcast，也无需安装 Homebrew。

Macbee 会在发送端 Mac 上采集全系统输出音频（包含系统通知和通话声音，不访问麦克风，也不保存任何录音文件），并在同一时间线上通过各台已选 Mac 的内置扬声器同步播放。

> **我需要自己安装 Snapcast 吗？**
>
> - **使用预编译的 `Macbee.app`：不需要。** 发送端和接收端 Mac 均不需要预先安装 Snapcast 或 Homebrew。`Macbee.app` 已内置经过 Macbee 定制补丁的 `snapserver`、`snapclient`、`snapclient-application`、`macbee-tone-probe` 和 `macbee-askpass`。接收端 Mac 甚至不需要安装 `Macbee.app`——当你在 Macbee 中点击 **Add Mac…** 添加设备时，应用会自动通过 SSH 将 3 个轻量级接收端组件部署到接收端用户的 `~/Library/Application Support/Macbee Receiver/` 目录中。
> - **从源码编译（开发者）：需要（仅限官方源码压缩包，不可用 Homebrew 替代）。** 源码构建通过 [`tools/release_backend.py`](tools/release_backend.py) 使用锁定哈希的 Snapcast `v0.35.0` 和 Boost `1.86.0` 源码归档包、Xcode Command Line Tools 与 CMake 完成。**切勿使用 `brew install snapcast` 替代**：Macbee 对上游 Snapcast 应用了定制的 V3 管道时钟/节奏恢复（pacing recovery）、Darwin 播放工作线程 QoS 以及 CoreAudio 统计补丁。

## 适用范围与系统要求

Macbee 目前专注于 Apple Silicon Mac 之间的**整机音频（Entire Mac）**同步共享。（单应用隔离 `One App` 模式已于 `1.2.6-dev` 中移除；Macbee 不支持蓝牙音箱/耳机输出、iOS 设备，也不承诺所有视频软件均能自动实现音画口型同步。）

| 角色 | 要求 |
| --- | --- |
| **发送端 Mac（Sender）** | • Apple Silicon（`arm64` / M 系列芯片），运行 **macOS 14.2 或更高版本**<br>• 已登录桌面图形会话<br>• 默认输出设备为 **Built-in Speakers（内置扬声器）**，采样率 **48 kHz** 立体声（且该输出设备无输入流）<br>• 系统 `/usr/bin/python3` **3.9 或更高版本** *（若未安装 Xcode Command Line Tools，macOS 会按需提示安装）* |
| **接收端 Mac（Receiver）**<br>*（最多 15 台远程 Mac + `This Mac` = 最多 16 个输出）* | • Apple Silicon（`arm64` / M 系列芯片），运行 **macOS 14.2 或更高版本**<br>• 已登录桌面图形会话（`/dev/console` 属主需与 SSH 登录账户一致）<br>• 默认输出设备为 **Built-in Speakers（内置扬声器）**，采样率 **48 kHz** 立体声<br>• **Python 3.9 或更高版本**（远程 SSH Shell `PATH` 中可用的 `python3`，例如通过 Xcode Command Line Tools 提供的 `/usr/bin/python3`）<br>• 已在系统设置中开启 **Remote Login（远程登录 / SSH）**——无需安装 `Macbee.app` |

---

## 快速上手（预编译 `Macbee.app`）

### 1. 在发送端 Mac 上安装 `Macbee.app`

1. 从 [GitHub Releases](https://github.com/podggg/macbee/releases) 下载并解压 `Macbee-*.zip`（例如 `Macbee-1.2.6-dev-arm64.zip`）。
2. 将 `Macbee.app` 拖入 `/Applications`（应用程序目录）。
3. **关于临时签名（Ad-hoc signing）的提示：** 当前 `1.2.6-dev` 构建采用临时签名（ad-hoc），尚未进行 Apple Developer ID 签名或公证。如果 macOS 拦截来自互联网下载的应用，请打开 **System Settings（系统设置）→ Privacy & Security（隐私与安全性）**，向下滚动找到拦截提示后点击 **Open Anyway（仍要打开）**。切勿关闭 Gatekeeper 或系统完整性保护（SIP）。

### 2. 在每台接收端 Mac 上开启 Remote Login（远程登录）

1. 在接收端 Mac 上打开 **System Settings（系统设置）→ General（通用）→ Sharing（共享）**，开启 **Remote Login（远程登录）**，并确认当前账户拥有访问权限。
2. 确保接收端 Mac 已登录桌面图形界面，且 **System Settings → Sound（声音）** 中的默认输出为 **Built-in Speakers（内置扬声器，48 kHz）**。
3. 如果不确定短用户名，可在接收端 Mac 的终端中运行 `whoami` 查看（例如 `alex`）。

### 3. 打开 Macbee 并点击 **Add Mac…**

1. 在发送端 Mac 上启动 `Macbee.app`。应用启动后保持空闲，不会自动采集音频或连接任何远程设备。
2. 点击 **Add Mac…**。首次点击时 macOS 可能会请求 **Local Network（本地网络）** 权限，用于发现局域网内开启 SSH 的设备（`_ssh._tcp`）；该权限可选，即使拒绝，仍可通过 `~/.ssh/config` 别名或手动输入地址添加。
3. 在弹出的 **Add a Mac** 面板中，可以通过以下三种方式之一选择接收端：
   - **Nearby Mac**（Bonjour `_ssh._tcp` 局域网发现）：选择列表中的设备（显示 `<host> · Nearby Mac`）。发送端本机显示为 **This Mac** 且不可重复添加；已配对过的设备标记为 **Added**。如果接收端短用户名与本机不同，可直接在 **MAC USERNAME** 输入框中修改。
   - **SSH config alias**：从 `~/.ssh/config` 中直接选择已有的 Host 别名（显示 `<user>@<host> · SSH config` 或 `SSH config alias`）。
   - **Enter address manually…**：选中该行后，在 **SSH ADDRESS** 中输入 `user@host`、`user@host:port` 或 SSH 别名，例如 `alex@192.168.1.20`、`alex@mac-mini.local` 或带方括号的 IPv6 地址 `alex@[fe80::1]:22`。**不要**在前面加上 `ssh `。
4. 可按需修改 **DEVICE NAME**（最长 60 个字符，将显示在主界面的 **OUTPUTS** 列表中）。

### 4. 点击 **Connect & Add** 完成 SSH 登录与部署

1. 点击 **Connect & Add**。
2. **首次连接确认主机指纹：** Macbee 会以 `StrictHostKeyChecking=ask` 调用系统 `/usr/bin/ssh`，并弹出 **Trust this SSH host key?** 对话框显示目标主机的 `SHA256:...` 指纹。核对无误后点击 **Trust and Continue**（如果遇到主机密钥意外变更，Macbee 会直接拒绝连接，绝不会篡改 `known_hosts`）。
3. **输入密码或密钥口令：** 在弹出的 **Sign in to the selected SSH destination** 安全输入框中填写接收端账户密码或 SSH 私钥口令，然后点击 **Continue**。口令仅通过内置的 `macbee-askpass` 辅助程序直接交给 `/usr/bin/ssh`，绝不写入磁盘、配置文件或日志。
4. *（可选）* 勾选 **Remember passphrase in Keychain**（默认关闭）后，Macbee 会通过 `ssh-add --apple-use-keychain` 将 **SSH 私钥口令** 存入 macOS 钥匙串。账户登录密码不会被保存。
5. Macbee 会自动检查接收端环境（Apple Silicon、macOS 14.2+、已登录桌面会话、48 kHz 内置扬声器），并将 3 个组件部署至接收端的 `~/Library/Application Support/Macbee Receiver/<digest>/`。新添加的 Mac 在 **OUTPUTS** 列表中默认为**未勾选**，且不会立即开始播放或采集声音。

### 5. 勾选输出设备并点击 **Start**

1. 在主界面的 **OUTPUTS** 区域，勾选希望同步播放的设备（如 **This Mac** 以及刚添加的远程 Mac）。开始前也可随时点击远程设备行右侧的 **Check** 按钮确认其状态为 **Available**。
2. 点击 **Start**。首次点击 **Start** 时，macOS 会请求 **System Audio Recording（系统音频录制）** 权限，请允许以启用整机音频采集。
3. 当状态变为 **Waiting for audio**（等待音频）或 **Playing**（播放中）后，在发送端 Mac 上用任意应用播放音频即可。会话期间静音或切换应用无需重新点击 Start；自由调节系统音量或静音也不会中断共享。
4. 结束时点击 **Stop**（或关闭窗口 / 退出 Macbee）。应用会先有序停止所有渲染器并关闭 SSH 控制主连接（ControlMaster），再释放系统音频采集。

---

## 常见问题与日常说明

- **连接 AirPods 或蓝牙耳机导致停止共享（`default_not_builtin`）：** Macbee 在发送端和接收端均强制执行内置扬声器安全策略（48 kHz 立体声 Built-in Speakers 且无输入流）。当 AirPods、蓝牙音箱或外接 USB 音频设备成为系统默认输出时，点击 **Start** 会被拒绝或正在进行的会话会安全停止（`default_not_builtin` / `unsupported_format` / `output_device_has_input_stream`）。请在 macOS“声音”设置中将默认输出切回“内置扬声器”后再点击 **Start**。
- **更新应用版本后重新弹出系统权限：** 由于预编译的 `Macbee.app` 采用临时签名（ad-hoc），用新版本替换 `/Applications/Macbee.app` 会改变代码签名身份，macOS 可能会再次提示 **Open Anyway**、**Local Network** 或 **System Audio Recording** 授权。按提示重新授权即可，切勿关闭 SIP 或 Gatekeeper。
- **接收端离线、休眠或 SSH 会话过期：**
  - 如果接收端的 SSH ControlMaster 连接因睡眠或重启应用而失效，点击 **Check** 或 **Start** 时会自动重新弹出应用内的 **Sign in to the selected SSH destination** 登录窗口（`auth_required`），无需重新添加设备。
  - 如果接收端处于锁屏注销状态、没有已登录的桌面会话（`receiver_needs_logged_in_session`），请先在该 Mac 屏幕上登录桌面。
  - 如果多设备共享过程中某一台远程接收端意外掉线，该设备状态会变为 **Offline**，其余已连接的设备会继续播放（若 **This Mac** 本机渲染器或共享服务端停止，则会安全结束整个会话）。
- **卸载与清理：**
  - **发送端 Mac：** 退出 Macbee，删除 `/Applications/Macbee.app`，并移除 `~/Library/Application Support/Macbee/`（包含 `local-config.json`、`diagnostics/` 与 `relay-runtime/`）。
  - **接收端 Mac：** 删除 `~/Library/Application Support/Macbee Receiver/`（如不再需要 SSH 访问，可在 **System Settings → General → Sharing** 中关闭 **Remote Login**）。
  - 临时 SSH 控制套接字仅在会话期间存在于 `/tmp/macbee-ctl.*` 与 `/tmp/macbee-auth.*`，在点击 Stop 或退出应用时自动清理。

---

## 从源码编译（开发者）

### 构建依赖

- Apple Silicon Mac，**macOS 14.2 或更高版本**，`/usr/bin/python3` **3.9+**，`/usr/bin/make`
- Xcode Command Line Tools（`xcrun clang` / `clang++`）
- `cmake`（例如通过 `brew install cmake` 安装——仅作构建工具，**切勿**使用 `brew install snapcast` 替代）

### 1. 编译带补丁的 Snapcast 后端（`tools/release_backend.py`）

创建一个干净、空置、无符号链接的真实绝对路径构建根目录 `$BUILD_ROOT`（所在磁盘分区需至少保留 **8.5 GiB** 可用空间，构建目录总占用不超过 **3.5 GiB**，首次解压 Boost 头文件时会额外检查约 2.25 GiB 空间预算）。将以下两个锁定哈希的上游归档包放入 `$BUILD_ROOT/downloads/`：

| 归档文件名 | 上游下载地址 | 要求 SHA-256 |
| --- | --- | --- |
| `snapcast-v0.35.0.tar.gz` | `https://github.com/snapcast/snapcast/archive/refs/tags/v0.35.0.tar.gz` | `cb75a71479bf52910bf5f47ae8120ec41c89459b0d77d7cd560e674e437ef050` |
| `boost_1_86_0.tar.bz2` | `https://archives.boost.io/release/1.86.0/source/boost_1_86_0.tar.bz2` | `1bed88e40401b2cb7a1f76d4bab499e352fa4d0c5f31c0dbae64e24d34d7513b` |

校验 SHA-256 后运行只读预检（`--plan`）及离线构建：

```sh
mkdir -p "$BUILD_ROOT/downloads"
curl -fL -o "$BUILD_ROOT/downloads/snapcast-v0.35.0.tar.gz" \
  https://github.com/snapcast/snapcast/archive/refs/tags/v0.35.0.tar.gz
curl -fL -o "$BUILD_ROOT/downloads/boost_1_86_0.tar.bz2" \
  https://archives.boost.io/release/1.86.0/source/boost_1_86_0.tar.bz2
shasum -a 256 "$BUILD_ROOT/downloads/snapcast-v0.35.0.tar.gz" "$BUILD_ROOT/downloads/boost_1_86_0.tar.bz2"

python3 -B tools/release_backend.py --root "$BUILD_ROOT" --cmake "$(command -v cmake)" --plan
python3 -B tools/release_backend.py --root "$BUILD_ROOT" --cmake "$(command -v cmake)"
```

构建完成后会生成 `$BUILD_ROOT/release-build/staging/`（包含 `bin/snapserver`、`bin/snapclient`、`bin/snapclient-application`、`licenses/`、`manifest.json`）以及对应的源码归档 `$BUILD_ROOT/release-build/macbee-backend-corresponding-source.tar.gz`。

### 2. 编译只读设备探测工具（`tools/build_probe.py`）

```sh
python3 tools/build_probe.py .local-app/probe-build
shasum -a 256 .local-app/probe-build/macbee-tone-probe
```

生成的 `macbee-tone-probe` SHA-256 须与 [`tools/speaker_transport.py`](tools/speaker_transport.py) 中的 `PROBE_HASH`（`8c5d81e619757e74e5d3406647e1603966563542171ad6cb74ecae874ada35e8`）一致。

### 3. 打包 `Macbee.app`（`tools/package_release.py --system-candidate`）

`--output` 必须指向 `.local-app/` 下尚不存在的新目录：

```sh
python3 tools/package_release.py \
  --system-candidate \
  --backend "$BUILD_ROOT/release-build/staging" \
  --probe .local-app/probe-build/macbee-tone-probe \
  --output .local-app/release-1.2.6-build10
```

该命令将输出 `.local-app/release-1.2.6-build10/Macbee.app`、`Macbee-1.2.6-dev-arm64.zip`、`SHA256SUMS`、`bundle-sha256.json` 及 `release-manifest.json`，并自动扫描确认无宿主机隐私路径泄露且通过临时签名校验。

### 4. 运行离线测试

```sh
python3 -m unittest discover -s Tests -p 'test_*.py'
python3 -Bu tools/test_pacing_headless.py python
python3 -Bu tools/test_pacing_headless.py native
```

如需针对 V3 后端舞台目录运行进程与 QoS 验证测试：

```sh
MACBEE_PACING_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_OFFLINE_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_QOS_STAGE="$BUILD_ROOT/release-build/staging" \
MACBEE_QOS_ARCHIVE="$BUILD_ROOT/downloads/snapcast-v0.35.0.tar.gz" \
python3 -Bu tools/test_pacing_headless.py stage
```

---

## 架构与更多文档

- [架构说明（ARCHITECTURE.md）](docs/ARCHITECTURE.md)——CoreAudio 采集、V3 节奏恢复、SSH ControlMaster 与安全守护机制
- [离线后端构建与对应源码合规说明（RELEASE-BACKEND.md）](docs/RELEASE-BACKEND.md)——Snapcast/Boost 编译配方与 GPLv3 分发核对
- [候选版本行为与已知限制（NEXT-USER-GUIDE.md）](docs/NEXT-USER-GUIDE.md)——随 App 内置的 Read Me
- [Macbee 配置与诊断 Agent 技能（skills/macbee-setup/SKILL.md）](skills/macbee-setup/SKILL.md)——面向 AI Agent 的安装、构建与排障指南

---

## 许可证与第三方声明

- **Macbee 第一方源码：** 采用 **`GPL-3.0-or-later`** 开源许可——详见 [`LICENSE`](LICENSE) 与 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)。
- **Snapcast `0.35.0`（打包及修改后的后端可执行文件——`snapserver`、`snapclient`、`snapclient-application`）：** 采用 **`GPL-3.0-or-later`** 许可（[上游仓库](https://github.com/snapcast/snapcast/tree/v0.35.0)）。原有上游版权声明与按 GPLv3 §5(a) 添加的文件级修改声明均完整保留，Macbee 的修改部分同样依据 `GPL-3.0-or-later` 提供且不附带任何担保。根据 GPLv3 §6(d)，每次分发二进制构建时，必须同时在同一 [GitHub Releases](https://github.com/podggg/macbee/releases) 页面中提供与之匹配的 **`macbee-backend-corresponding-source.tar.gz`** 对应源码归档包，保持同等访问条件且不得附加额外限制。
- **Boost `1.86.0`（编译 Snapcast 所用头文件）：** 采用 **Boost Software License 1.0（`BSL-1.0`）**。
- **Snapcast 内嵌的纯头文件库：** `aixlog` `1.5.1`、`popl` `1.3.1` 与 `nlohmann/json` `3.11.3` 均采用 **MIT License**。
- **仅通过 GitHub Releases 直接分发（不兼容 Mac App Store）：** 由于 Macbee 及其内置的 Snapcast 后端采用 `GPL-3.0-or-later` 许可，项目仅通过 [GitHub Releases](https://github.com/podggg/macbee/releases) 随同对应源码归档包直接分发，不兼容 Mac App Store 分发条款。

打包后的 `Macbee.app` 在 `Macbee.app/Contents/Resources/Licenses/` 目录中包含完整的许可文本与声明文件（`GPL-3.0.txt`、`BSL-1.0.txt`、`MIT-components.txt`、`THIRD-PARTY-NOTICES.txt`）。
