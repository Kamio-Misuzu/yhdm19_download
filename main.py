import sys
import os
import re
import json
import time
import shutil
import binascii
import base64
import subprocess
import requests
import urllib3
from lxml import etree
from urllib.parse import urlparse, urljoin ,parse_qs
from Crypto.Cipher import AES
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QListWidget, QProgressBar, QFileDialog,
                             QCheckBox, QMessageBox, QGroupBox, QTextEdit, QListWidgetItem)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPalette, QColor, QIcon

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class DownloadThread(QThread):
    """下载线程类，用于在后台执行下载任务"""
    progress_updated = pyqtSignal(int, int, int)  # 当前片段, 总片段, 进度百分比
    log_message = pyqtSignal(str)
    download_finished = pyqtSignal(str, bool, str)  # 剧集名称, 是否成功, 消息
    current_episode = pyqtSignal(str)

    def __init__(self, episodes, output_dir, convert_to_mp4):
        super().__init__()
        self.episodes = episodes
        self.output_dir = output_dir
        self.convert_to_mp4 = convert_to_mp4
        self.is_canceled = False

    def run(self):
        """执行下载任务"""
        for ep in self.episodes:
            if self.is_canceled:
                break

            self.current_episode.emit(ep['name'])
            self.log_message.emit(f"\n开始下载: {ep['name']}")

            # 获取视频信息
            video_info = self.get_datas(ep['url'])
            if not video_info:
                self.download_finished.emit(ep['name'], False, "无法获取视频信息")
                continue

            # 创建文件名
            episode_name = re.sub(r'[\\/*?:"<>|]', '', ep['name'])
            ts_file = os.path.join(self.output_dir, f"{episode_name}.ts")
            mp4_file = os.path.join(self.output_dir, f"{episode_name}.mp4")

            # 检查文件是否已存在
            if os.path.exists(ts_file) or (self.convert_to_mp4 and os.path.exists(mp4_file)):
                self.log_message.emit(f"文件已存在，跳过下载: {episode_name}")
                self.download_finished.emit(ep['name'], True, "文件已存在")
                continue

            # 下载视频
            success, message = self.download_m3u8_video(
                video_info['m3u8_url'],
                ts_file,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                                  'Chrome/87.0.4280.141 Safari/537.36 Edg/87.0.664.75',
                    'Referer': ep['url']
                }
            )

            if success:
                self.log_message.emit(f"成功下载: {ts_file}")

                # 转换为MP4格式
                if self.convert_to_mp4:
                    if self.convert_to_mp4_file(ts_file, mp4_file):
                        # 删除原始TS文件
                        try:
                            os.remove(ts_file)
                            self.log_message.emit(f"已删除临时文件: {ts_file}")
                            self.download_finished.emit(ep['name'], True, f"已保存为MP4格式: {mp4_file}")
                        except:
                            self.download_finished.emit(ep['name'], True, f"保留TS文件: {ts_file}")
                    else:
                        self.download_finished.emit(ep['name'], True, f"转换失败，保留TS文件: {ts_file}")
                else:
                    self.download_finished.emit(ep['name'], True, f"已保存为TS格式: {ts_file}")
            else:
                self.download_finished.emit(ep['name'], False, message)

    def get_datas(self, url):
        """从播放页面提取视频信息"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.141 Safari/537.36 Edg/87.0.664.75',
                'Referer': 'https://www.yhdm19.cc/'
            }

            rsp = requests.get(url, headers=headers, timeout=10, verify=False)
            rsp.raise_for_status()
            rsp_HTML = etree.HTML(rsp.text)

            # 处理樱花动漫网站
            # 方法1: 尝试从player_aaaa脚本中提取
            scripts = rsp_HTML.xpath("//script[contains(text(), 'var player_aaaa')]/text()")
            if scripts:
                script_content = scripts[0]
                self.log_message.emit("找到player_aaaa脚本")

                # 提取player_aaaa对象
                pattern = re.compile(r'var player_aaaa\s*=\s*({.*?});', re.DOTALL)
                match = pattern.search(script_content)
                if match:
                    try:
                        player_json = match.group(1)
                        player_json = player_json.replace("\\'", "'").replace('\\"', '"')
                        player_data = json.loads(player_json)

                        vod_name = player_data.get('vod_data', {}).get('vod_name', 'video')
                        m3u8_url = player_data.get('url', '')

                        if m3u8_url:
                            self.log_message.emit(f"从player_aaaa提取到m3u8地址: {m3u8_url}")
                            return {'title': vod_name, 'm3u8_url': m3u8_url}
                    except json.JSONDecodeError:
                        # 尝试手动提取
                        m3u8_match = re.search(r'"url"\s*:\s*"([^"]+)"', player_json)
                        if m3u8_match:
                            m3u8_url = m3u8_match.group(1)
                            self.log_message.emit(f"手动提取m3u8地址: {m3u8_url}")
                            return {'title': 'video', 'm3u8_url': m3u8_url}

            self.log_message.emit("从player_aaaa脚本中提取失败")

            # 尝试从iframe的src属性中提取
            iframes = rsp_HTML.xpath("//div[contains(@class, 'MacPlayer')]//iframe/@src")
            for iframe_src in iframes:
                if "jiexi.modujx01.com" in iframe_src:
                    iframe_rsp = requests.get(iframe_src,
                                             headers=headers,
                                             timeout=10,
                                             verify=False)
                    iframe_rsp.raise_for_status()
                    iframe_html = etree.HTML(iframe_rsp.text)
                    # 首先尝试找 <video><source src="...m3u8"></video>
                    m3u8_list = iframe_html.xpath("//video/source[@src]/@src")
                    if m3u8_list:
                        m3u8_url = m3u8_list[0]
                        self.log_message.emit(f"从解析页 video 标签提取到 m3u8: {m3u8_url}")
                        return {'title': vod_name, 'm3u8_url': m3u8_url}

                    # 如果没找到，再在所有 <script> 里匹配一次
                    scripts2 = iframe_html.xpath("//script/text()")
                    for sc in scripts2:
                        m = re.search(r'(https?://[^\s"\']+?\.m3u8[^\s"\']*)', sc)
                        if m:
                            m3u8_url = m.group(1)
                            self.log_message.emit(f"从解析页脚本提取到 m3u8: {m3u8_url}")
                            return {'title': vod_name, 'm3u8_url': m3u8_url}

            self.log_message.emit("从iframe的src属性中提取失败")


            # 尝试从视频播放器配置中提取
            config_scripts = rsp_HTML.xpath("//script[contains(@src, 'playerconfig')]/@src")
            if config_scripts:
                config_url = urljoin(url, config_scripts[0])
                config_rsp = requests.get(config_url, headers=headers, timeout=10, verify=False)
                if config_rsp.status_code == 200:
                    config_content = config_rsp.text
                    m3u8_match = re.search(r'url:\s*["\'](https?://[^\s"\']+?\.m3u8[^\s"\']*)["\']', config_content)
                    if m3u8_match:
                        m3u8_url = m3u8_match.group(1).replace('\\/', '/')
                        self.log_message.emit(f"从播放器配置提取到m3u8地址: {m3u8_url}")
                        return {'title': vod_name, 'm3u8_url': m3u8_url}

            self.log_message.emit("视频播放器配置中提取失败")


            # 尝试直接搜索m3u8链接
            all_scripts = rsp_HTML.xpath("//script/text()")
            for script in all_scripts:
                if '.m3u8' in script:
                    m3u8_match = re.search(r'(http[^\s"]+?\.m3u8[^\s"]*)', script)
                    if m3u8_match:
                        m3u8_url = m3u8_match.group(1)
                        self.log_message.emit(f"直接找到m3u8地址: {m3u8_url}")
                        # 获取视频名称
                        title = rsp_HTML.xpath("//div[@class='stui-player__detail detail']/h4/text()")
                        if title:
                            title = title[0].strip()
                        else:
                            title = "video"
                        return {'title': title, 'm3u8_url': m3u8_url}
            self.log_message.emit("直接搜索m3u8链接方式失败")
            return None
        except Exception as e:
            self.log_message.emit(f"获取视频信息失败: {str(e)}")
            return None

    def download_m3u8_video(self, m3u8_url, output_file, headers=None):
        """下载并合并m3u8视频，支持解密"""
        try:
            if headers is None:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.141 Safari/537.36 Edg/87.0.664.75',
                    'Referer': 'https://www.yhdm19.cc/'
                }

            # 修复m3u8 URL中的转义斜杠
            m3u8_url = m3u8_url.replace('\\/', '/')

            self.log_message.emit(f"获取m3u8文件: {m3u8_url}")
            rsp = requests.get(m3u8_url, headers=headers, timeout=30, verify=False)
            rsp.raise_for_status()
            rsp_text = rsp.text

            if "#EXTM3U" not in rsp_text:
                self.log_message.emit("这不是一个有效的m3u8文件！")
                return False, "无效的m3u8文件"

            # 检查是否加密
            key_uri = None
            iv = None
            key = None
            if "EXT-X-KEY" in rsp_text:
                self.log_message.emit("视频已加密，尝试解密...")
                key_match = re.search(r'#EXT-X-KEY:METHOD=AES-128,URI="([^"]+)"(?:,IV=([^,]+))?', rsp_text)
                if key_match:
                    key_uri = key_match.group(1)
                    iv_str = key_match.group(2)

                    self.log_message.emit(f"找到加密密钥URI: {key_uri}")
                    self.log_message.emit(f"IV字符串: {iv_str}")

                    # 处理IV
                    if iv_str:
                        try:
                            iv_str = iv_str.strip()
                            if iv_str.startswith('0x') or iv_str.startswith('0X'):
                                iv_str = iv_str[2:]
                            iv = binascii.unhexlify(iv_str)
                            self.log_message.emit(f"IV解析为十六进制: {iv.hex()}")
                        except binascii.Error:
                            try:
                                iv = base64.b64decode(iv_str)
                                self.log_message.emit(f"IV解析为Base64: {iv.hex()}")
                            except:
                                iv = iv_str.encode('utf-8')[:16]
                                self.log_message.emit(f"IV直接使用字符串: {iv.hex()}")
                    else:
                        iv = b'\x00' * 16
                        self.log_message.emit(f"使用默认IV: {iv.hex()}")

                    # 确保IV长度为16字节
                    if len(iv) != 16:
                        if len(iv) > 16:
                            iv = iv[:16]
                            self.log_message.emit(f"截断IV至16字节: {iv.hex()}")
                        else:
                            iv = iv.ljust(16, b'\x00')
                            self.log_message.emit(f"填充IV至16字节: {iv.hex()}")

                    # 下载密钥
                    try:
                        if not key_uri.startswith('http'):
                            base_url = m3u8_url.rsplit('/', 1)[0] + '/'
                            key_url = urljoin(base_url, key_uri)
                        else:
                            key_url = key_uri

                        self.log_message.emit(f"下载密钥: {key_url}")
                        key_rsp = requests.get(key_url, headers=headers, timeout=30, verify=False)
                        key_rsp.raise_for_status()
                        key = key_rsp.content
                        self.log_message.emit(f"密钥下载成功，长度: {len(key)}字节")

                        if len(key) != 16:
                            self.log_message.emit(f"密钥长度不正确 ({len(key)}字节)，尝试修正...")
                            if len(key) > 16:
                                key = key[:16]
                                self.log_message.emit(f"截断密钥至16字节")
                            else:
                                key = key.ljust(16, b'\x00')
                                self.log_message.emit(f"填充密钥至16字节")
                    except Exception as e:
                        self.log_message.emit(f"下载密钥失败: {str(e)}")
                        return False, "下载密钥失败"
                else:
                    self.log_message.emit("无法提取密钥信息")
                    return False, "无法提取密钥信息"
            else:
                self.log_message.emit("视频未加密")

            # 提取所有ts片段
            base_url = m3u8_url.rsplit('/', 1)[0] + '/'
            ts_list = []
            for line in rsp_text.split('\n'):
                if line.strip() and not line.startswith('#') and (line.endswith('.ts') or '.ts?' in line):
                    ts_list.append(line if line.startswith('http') else urljoin(base_url, line))

            self.log_message.emit(f"发现 {len(ts_list)} 个视频片段")

            # 如果 master playlist，先去解析子 playlist
            if len(ts_list) == 0:
                # 找出所有子 m3u8 路径
                variants = [ln.strip() for ln in rsp_text.split('\n')
                            if ln.strip() and not ln.startswith('#') and ln.endswith('.m3u8')]
                if variants:
                    # 这里简单取第一个 variant，你也可以按带宽/分辨率挑最合适的
                    variant_url = variants[0]
                    variant_url = variant_url if variant_url.startswith('http') else urljoin(base_url, variant_url)
                    self.log_message.emit(f"这是 master playlist，切换到子 playlist：{variant_url}")
                    # 二次请求子 m3u8
                    rsp2 = requests.get(variant_url, headers=headers, timeout=30, verify=False)
                    rsp2.raise_for_status()
                    rsp_text = rsp2.text
                    # 重构 base_url 与 ts_list
                    base_url = variant_url.rsplit('/', 1)[0] + '/'
                    ts_list = [ln if ln.startswith('http') else urljoin(base_url, ln)
                               for ln in rsp_text.split('\n')
                               if ln.strip() and not ln.startswith('#') and (ln.endswith('.ts') or '.ts?' in ln)]
                    self.log_message.emit(f"子 playlist 发现 {len(ts_list)} 个视频片段")

            if len(ts_list) == 0:
                self.log_message.emit("最终仍未找到有效的视频片段")
                return False, "未找到有效的视频片段"

            # 创建临时目录保存片段
            temp_dir = os.path.join(os.path.dirname(output_file), f"{os.path.basename(output_file)}_temp")
            os.makedirs(temp_dir, exist_ok=True)

            # 下载所有ts片段
            self.log_message.emit("开始下载视频片段...")
            total_segments = len(ts_list)
            segment_files = {}

            # 第一轮下载
            for i, ts_url in enumerate(ts_list, 1):
                if self.is_canceled:
                    return False, "用户取消下载"

                # 更新进度
                progress = int((i / total_segments) * 100)
                self.progress_updated.emit(i, total_segments, progress)

                ts_file = os.path.join(temp_dir, f"{i:04d}.ts")
                segment_files[i] = ts_file

                # 检查片段是否已存在
                if os.path.exists(ts_file) and os.path.getsize(ts_file) > 0:
                    self.log_message.emit(f"片段 {i}/{total_segments} 已存在，跳过下载")
                    continue

                self.log_message.emit(f"下载片段 {i}/{total_segments}: {ts_url}")

                retry = 0
                success = False
                while retry < 5 and not success and not self.is_canceled:
                    try:
                        rsp = requests.get(ts_url, headers=headers, timeout=30, verify=False)
                        if rsp.status_code == 200:
                            content = rsp.content
                            if key is not None:
                                try:
                                    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
                                    content = cipher.decrypt(content)

                                    last_non_zero = len(content) - 1
                                    while last_non_zero >= 0 and content[last_non_zero] == 0:
                                        last_non_zero -= 1

                                    if last_non_zero < len(content) - 1:
                                        content = content[:last_non_zero + 1]
                                except Exception as e:
                                    self.log_message.emit(f"解密失败: {str(e)}")

                            with open(ts_file, 'wb') as f:
                                f.write(content)

                            # 验证文件大小
                            if os.path.getsize(ts_file) > 0:
                                success = True
                                self.log_message.emit(f"片段 {i} 下载成功")
                            else:
                                os.remove(ts_file)
                                self.log_message.emit(f"片段 {i} 下载失败: 文件大小为0")
                                retry += 1
                                time.sleep(1)
                        else:
                            self.log_message.emit(f"下载失败，状态码: {rsp.status_code}")
                            retry += 1
                            time.sleep(1)
                    except Exception as e:
                        self.log_message.emit(f"下载失败: {str(e)}")
                        retry += 1
                        time.sleep(2)

                if not success and not self.is_canceled:
                    self.log_message.emit(f"片段 {i} 下载失败，稍后重试")

            # 检查缺失的片段并进行重试
            max_retries = 5
            retry_count = 0
            missing_segments = []

            while retry_count < max_retries and not self.is_canceled:
                # 检查哪些片段缺失
                missing_segments = []
                for i in range(1, total_segments + 1):
                    ts_file = segment_files[i]
                    if not os.path.exists(ts_file) or os.path.getsize(ts_file) == 0:
                        missing_segments.append((i, ts_list[i - 1]))

                if not missing_segments:
                    self.log_message.emit("所有片段下载完成")
                    break

                retry_count += 1
                self.log_message.emit(f"第 {retry_count} 次重试缺失片段 ({len(missing_segments)}个)")

                # 下载缺失的片段
                for idx, (segment_num, ts_url) in enumerate(missing_segments, 1):
                    if self.is_canceled:
                        return False, "用户取消下载"

                    # 更新进度
                    progress = int((segment_num / total_segments) * 100)
                    self.progress_updated.emit(segment_num, total_segments, progress)

                    ts_file = segment_files[segment_num]
                    self.log_message.emit(f"重试片段 {segment_num}/{total_segments}: {ts_url}")

                    retry = 0
                    success = False
                    while retry < 5 and not success and not self.is_canceled:
                        try:
                            rsp = requests.get(ts_url, headers=headers, timeout=30, verify=False)
                            if rsp.status_code == 200:
                                content = rsp.content
                                if key is not None:
                                    try:
                                        cipher = AES.new(key, AES.MODE_CBC, iv=iv)
                                        content = cipher.decrypt(content)

                                        last_non_zero = len(content) - 1
                                        while last_non_zero >= 0 and content[last_non_zero] == 0:
                                            last_non_zero -= 1

                                        if last_non_zero < len(content) - 1:
                                            content = content[:last_non_zero + 1]
                                    except Exception as e:
                                        self.log_message.emit(f"解密失败: {str(e)}")

                                with open(ts_file, 'wb') as f:
                                    f.write(content)

                                # 验证文件大小
                                if os.path.getsize(ts_file) > 0:
                                    success = True
                                    self.log_message.emit(f"片段 {segment_num} 重试成功")
                                else:
                                    os.remove(ts_file)
                                    self.log_message.emit(f"片段 {segment_num} 重试失败: 文件大小为0")
                                    retry += 1
                                    time.sleep(1)
                            else:
                                self.log_message.emit(f"重试失败，状态码: {rsp.status_code}")
                                retry += 1
                                time.sleep(1)
                        except Exception as e:
                            self.log_message.emit(f"重试失败: {str(e)}")
                            retry += 1
                            time.sleep(2)

                    if not success and not self.is_canceled:
                        self.log_message.emit(f"片段 {segment_num} 重试失败")

                # 每次重试后等待一下
                time.sleep(1)

            # 检查是否还有缺失片段
            final_missing = []
            for i in range(1, total_segments + 1):
                ts_file = segment_files[i]
                if not os.path.exists(ts_file) or os.path.getsize(ts_file) == 0:
                    final_missing.append(i)

            if final_missing:
                self.log_message.emit(f"警告: 仍有 {len(final_missing)} 个片段缺失: {final_missing}")
                self.log_message.emit("将尝试合并现有片段，但视频可能不完整")

            # 合并所有ts文件
            self.log_message.emit(f"合并视频片段到: {output_file}")
            with open(output_file, 'wb') as outfile:
                for i in range(1, total_segments + 1):
                    ts_file = segment_files[i]
                    if os.path.exists(ts_file) and os.path.getsize(ts_file) > 0:
                        try:
                            with open(ts_file, 'rb') as infile:
                                shutil.copyfileobj(infile, outfile)
                        except Exception as e:
                            self.log_message.emit(f"合并片段 {i} 失败: {str(e)}")
                    else:
                        self.log_message.emit(f"跳过缺失的片段: {i}")

            # 清理临时文件
            self.log_message.emit("清理临时文件...")
            for i in range(1, total_segments + 1):
                ts_file = segment_files[i]
                if os.path.exists(ts_file):
                    try:
                        os.remove(ts_file)
                    except:
                        pass
            try:
                os.rmdir(temp_dir)
            except:
                pass

            if final_missing:
                self.log_message.emit(f"视频下载完成但有缺失片段: {output_file}")
                return True, f"下载完成但有{len(final_missing)}个片段缺失"
            else:
                self.log_message.emit(f"视频下载完成: {output_file}")
                return True, "下载成功"
        except Exception as e:
            self.log_message.emit(f"下载过程中发生错误: {str(e)}")
            return False, str(e)

    def convert_to_mp4_file(self, input_file, output_file):
        """将TS文件转换为MP4格式"""
        try:
            self.log_message.emit(f"转换视频格式: {input_file} -> {output_file}")

            # 使用FFmpeg进行转换
            command = [
                'ffmpeg',
                '-y',
                '-i', input_file,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                output_file
            ]

            # 运行FFmpeg
            result = subprocess.run(command, capture_output=True, text=True)

            if result.returncode == 0:
                self.log_message.emit(f"转换成功: {output_file}")
                return True
            else:
                self.log_message.emit(f"转换失败，错误代码: {result.returncode}")
                self.log_message.emit(f"错误信息: {result.stderr}")
                return False
        except Exception as e:
            self.log_message.emit(f"转换失败: {str(e)}")
            return False

    def cancel(self):
        """取消下载"""
        self.is_canceled = True
        self.log_message.emit("用户取消下载...")


class AnimeDownloaderApp(QMainWindow):
    """樱花动漫视频下载器主界面"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("樱花动漫视频下载器")
        self.setGeometry(460, 100, 1000, 850)  # 窗口宽高
        icon_path = "F:/python/MC/爬虫/640x640.ico"
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            print(f"图标不存在: {icon_path}")
        # 初始化UI
        self.init_ui()

        # 初始化变量
        self.download_thread = None
        self.episodes = []
        self.video_title = ""

        # 检查FFmpeg是否可用
        self.check_ffmpeg()

    def init_ui(self):
        """初始化用户界面"""
        # 主布局
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout()
        main_widget.setLayout(main_layout)

        # 左侧区域（垂直布局）
        left_layout = QVBoxLayout()
        main_layout.addLayout(left_layout, 40)  # 设置宽度

        # 软件说明
        info_group = QGroupBox("软件说明")
        info_layout = QVBoxLayout()

        info_text = QLabel(
            "欢迎使用樱花动漫视频下载器！\n"
            "该脚本由b站-下北泽虹夏制作\n"
            "本软件用于下载樱花动漫网站的视频内容。\n"
            "使用步骤：\n"
            "1. 输入视频播放页URL\n"
            "2. 点击'获取剧集'按钮\n"
            "3. 选择要下载的剧集\n"
            "4. 设置保存路径\n"
            "5. 点击'开始下载'按钮\n"
            "\n注意1: 下载加密视频需要安装pycryptodome库，转换MP4需要安装FFmpeg\n"
            "注意2: 同时如果下载出现问题, 请检查网络并且查看本地是否可以访问该网站\n"
            "注意3: 樱花动漫有多个不同的频道, 本软件支持所有频道(A、B、C等)")

        info_text.setFont(QFont("Arial", 7))
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)
        info_group.setLayout(info_layout)
        left_layout.addWidget(info_group)

        # URL输入区域
        url_group = QGroupBox("视频URL")
        url_layout = QVBoxLayout()

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "请输入视频播放页URL，例如: https://www.yhdm19.cc/")
        url_layout.addWidget(self.url_input)

        url_btn_layout = QHBoxLayout()

        self.fetch_btn = QPushButton("获取剧集")
        self.fetch_btn.clicked.connect(self.fetch_episodes)
        url_btn_layout.addWidget(self.fetch_btn)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.clear_all)
        url_btn_layout.addWidget(self.clear_btn)

        url_layout.addLayout(url_btn_layout)
        url_group.setLayout(url_layout)
        left_layout.addWidget(url_group)

        # 视频标题显示
        self.title_label = QLabel("视频标题: 未获取")
        self.title_label.setFont(QFont("Arial", 10, QFont.Bold))
        left_layout.addWidget(self.title_label)

        # 剧集列表
        episodes_group = QGroupBox("剧集列表")
        episodes_layout = QVBoxLayout()

        # 按序号选择的功能
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel("开始序号:"))
        self.start_index_input = QLineEdit()
        self.start_index_input.setPlaceholderText("开始序号")
        self.start_index_input.setFixedWidth(60)
        range_layout.addWidget(self.start_index_input)

        range_layout.addWidget(QLabel("结束序号:"))
        self.end_index_input = QLineEdit()
        self.end_index_input.setPlaceholderText("结束序号")
        self.end_index_input.setFixedWidth(60)
        range_layout.addWidget(self.end_index_input)

        self.select_range_btn = QPushButton("按序号选择")
        self.select_range_btn.clicked.connect(self.select_by_range)
        range_layout.addWidget(self.select_range_btn)

        range_layout.addStretch()
        episodes_layout.addLayout(range_layout)

        self.episodes_list = QListWidget()
        self.episodes_list.setSelectionMode(QListWidget.MultiSelection)
        episodes_layout.addWidget(self.episodes_list)

        episodes_btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(self.select_all)
        episodes_btn_layout.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("取消全选")
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        episodes_btn_layout.addWidget(self.deselect_all_btn)

        episodes_layout.addLayout(episodes_btn_layout)
        episodes_group.setLayout(episodes_layout)
        left_layout.addWidget(episodes_group)

        # 下载进度
        progress_group = QGroupBox("下载进度")
        progress_layout = QVBoxLayout()

        self.current_episode_label = QLabel("当前下载: 无")
        progress_layout.addWidget(self.current_episode_label)

        self.progress_label = QLabel("进度: 0/0 (0%)")
        progress_layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        progress_group.setLayout(progress_layout)
        left_layout.addWidget(progress_group)

        # 设置区域
        settings_group = QGroupBox("设置")
        settings_layout = QVBoxLayout()

        # 保存路径
        path_layout = QHBoxLayout()
        self.path_label = QLabel("保存路径:")
        path_layout.addWidget(self.path_label)

        self.path_input = QLineEdit()
        self.path_input.setText(os.path.expanduser("~/Downloads"))
        path_layout.addWidget(self.path_input)

        self.browse_btn = QPushButton("浏览...")
        self.browse_btn.clicked.connect(self.browse_path)
        path_layout.addWidget(self.browse_btn)

        settings_layout.addLayout(path_layout)

        # 格式转换
        self.convert_checkbox = QCheckBox("将下载的视频转换为MP4格式")
        self.convert_checkbox.setChecked(True)
        settings_layout.addWidget(self.convert_checkbox)

        settings_group.setLayout(settings_layout)
        left_layout.addWidget(settings_group)

        # 底部按钮
        btn_layout = QHBoxLayout()
        self.download_btn = QPushButton("开始下载")
        self.download_btn.clicked.connect(self.start_download)
        self.download_btn.setEnabled(False)
        btn_layout.addWidget(self.download_btn)

        self.cancel_btn = QPushButton("取消下载")
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.cancel_btn.setEnabled(False)
        btn_layout.addWidget(self.cancel_btn)

        left_layout.addLayout(btn_layout)

        # 右侧区域 - 操作日志
        log_group = QGroupBox("操作日志")
        log_layout = QVBoxLayout()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group, 60)  # 日志宽度

        # 状态栏
        self.statusBar().showMessage("就绪")

        # 设置样式
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid gray;
                border-radius: 5px;
                margin-top: 1ex;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px;
            }
            QPushButton {
                padding: 5px 10px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
            QProgressBar {
                border: 1px solid grey;
                border-radius: 5px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #05B8CC;
                width: 10px;
            }
            QLineEdit {
                padding: 3px;
            }
        """)

        # 日志区域样式
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #f0f0f0;
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 5px;
            }
        """)

    def check_ffmpeg(self):
        """检查FFmpeg是否可用"""
        try:
            result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
            if result.returncode == 0:
                self.log("FFmpeg已安装，可以转换为MP4格式")
                self.convert_checkbox.setEnabled(True)
            else:
                self.log("警告: 未找到FFmpeg，无法转换为MP4格式")
                self.convert_checkbox.setEnabled(False)
        except FileNotFoundError:
            self.log("警告: 未找到FFmpeg，无法转换为MP4格式")
            self.convert_checkbox.setEnabled(False)

    def log(self, message):
        """添加日志消息"""
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        self.log_text.append(f"[{timestamp}] {message}")
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
        self.statusBar().showMessage(message)

    def fetch_episodes(self):
        """获取剧集列表"""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "输入错误", "请输入视频播放页URL")
            return

        self.log(f"获取剧集信息: {url}")
        self.fetch_btn.setEnabled(False)
        self.url_input.setEnabled(False)

        try:
            # 获取视频标题
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/87.0.4280.141 Safari/537.36 Edg/87.0.664.75'
            }
            rsp = requests.get(url, headers=headers, timeout=10, verify=False)
            rsp.raise_for_status()
            rsp_HTML = etree.HTML(rsp.text)

            # 尝试提取标题
            title = rsp_HTML.xpath("//h4[@class='title']/text()")
            if title:
                self.video_title = title[0].strip()
            else:
                title = rsp_HTML.xpath("//div[contains(@class, 'stui-player__detail')]/h4/text()")
                if title:
                    self.video_title = title[0].strip()
                else:
                    title = rsp_HTML.xpath("//title/text()")
                    if title:
                        self.video_title = title[0].split('-')[0].strip()
                    else:
                        self.video_title = "video"

            self.title_label.setText(f"视频标题: {self.video_title}")

            # 获取所有剧集信息
            parsed_uri = urlparse(url)
            host = '{uri.scheme}://{uri.netloc}'.format(uri=parsed_uri)

            # 查找所有播放源
            playlists = rsp_HTML.xpath(
                "//div[contains(@class, 'stui-pannel') and .//h3[contains(@class, 'title')] and .//ul[contains(@class, 'stui-content__playlist')]]")

            if not playlists:
                self.log("未找到剧集信息，尝试备用解析方法...")
                playlists = rsp_HTML.xpath("//div[contains(@class, 'stui-pannel')]")

            self.log(f"找到 {len(playlists)} 个播放源")

            self.episodes = []
            for playlist in playlists:
                # 获取播放源名称 (A, B, C等)
                source_name = playlist.xpath(".//h3[@class='title']/text()")
                if not source_name:
                    continue
                source_name = source_name[0].strip()

                if "随机推荐" in source_name:
                    continue

                self.log(f"处理播放源: {source_name}")

                # 获取该播放源下的所有剧集
                items = playlist.xpath(".//a")
                self.log(f"此播放源有 {len(items)} 个剧集")

                for item in items:
                    episode_name = item.xpath("./text()")
                    if not episode_name:
                        continue
                    episode_name = episode_name[0].strip()

                    episode_url = item.xpath("./@href")
                    if not episode_url:
                        continue
                    episode_url = episode_url[0]

                    if not episode_url.startswith('http'):
                        episode_url = urljoin(host, episode_url)

                    if "/vod/play/" in episode_url:
                        self.episodes.append({
                            'source': source_name,
                            'name': f"[{source_name}] {episode_name}",
                            'url': episode_url
                        })

            if self.episodes:
                self.log(f"发现 {len(self.episodes)} 个剧集")
                self.episodes_list.clear()
                for ep in self.episodes:
                    item = QListWidgetItem(ep['name'])
                    self.episodes_list.addItem(item)
                self.download_btn.setEnabled(True)
            else:
                self.log("未找到剧集信息")
                QMessageBox.warning(self, "获取失败", "未找到剧集信息，请检查URL是否正确")

        except Exception as e:
            self.log(f"获取剧集失败: {str(e)}")
            QMessageBox.critical(self, "错误", f"获取剧集失败: {str(e)}")

        finally:
            self.fetch_btn.setEnabled(True)
            self.url_input.setEnabled(True)

    def select_by_range(self):
        """按序号范围选择剧集"""
        try:
            start_index = int(self.start_index_input.text().strip()) - 1
            end_index = int(self.end_index_input.text().strip()) - 1

            if start_index < 0 or end_index < 0:
                QMessageBox.warning(self, "输入错误", "序号必须大于0")
                return

            if start_index > end_index:
                QMessageBox.warning(self, "输入错误", "开始序号不能大于结束序号")
                return

            if end_index >= self.episodes_list.count():
                QMessageBox.warning(self, "输入错误", "结束序号超出范围")
                return

            self.episodes_list.clearSelection()
            for i in range(start_index, end_index + 1):
                self.episodes_list.item(i).setSelected(True)

            self.log(f"已选择剧集 {start_index + 1} 到 {end_index + 1}")
        except ValueError:
            QMessageBox.warning(self, "输入错误", "请输入有效的序号")

    def select_all(self):
        """选择所有剧集"""
        self.episodes_list.selectAll()
        self.log("已选择所有剧集")

    def deselect_all(self):
        """取消选择所有剧集"""
        self.episodes_list.clearSelection()
        self.log("已取消选择所有剧集")

    def browse_path(self):
        """浏览保存路径"""
        path = QFileDialog.getExistingDirectory(self, "选择保存路径", self.path_input.text())
        if path:
            self.path_input.setText(path)
            self.log(f"保存路径设置为: {path}")

    def start_download(self):
        """开始下载选中的剧集"""
        selected_items = self.episodes_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "选择错误", "请选择至少一个剧集")
            return

        output_dir = self.path_input.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "路径错误", "请设置保存路径")
            return

        # 创建保存目录
        dir_name = re.sub(r'[\\/*?:"<>|]', '', self.video_title)
        output_dir = os.path.join(output_dir, dir_name)
        try:
            os.makedirs(output_dir, exist_ok=True)
            self.log(f"视频将保存到目录: {output_dir}")
        except Exception as e:
            self.log(f"创建目录失败: {str(e)}")
            QMessageBox.critical(self, "错误", f"创建目录失败: {str(e)}")
            return

        # 获取选中的剧集
        selected_episodes = []
        for item in selected_items:
            index = self.episodes_list.row(item)
            selected_episodes.append(self.episodes[index])

        self.log(f"开始下载 {len(selected_episodes)} 个剧集...")

        # 重置进度条
        self.progress_bar.setValue(0)
        self.progress_label.setText("进度: 0/0 (0%)")

        # 创建下载线程
        self.download_thread = DownloadThread(
            selected_episodes,
            output_dir,
            self.convert_checkbox.isChecked()
        )

        # 连接信号
        self.download_thread.progress_updated.connect(self.update_progress)
        self.download_thread.log_message.connect(self.log)
        self.download_thread.download_finished.connect(self.download_finished)
        self.download_thread.current_episode.connect(self.current_episode_label.setText)

        # 更新UI状态
        self.fetch_btn.setEnabled(False)
        self.url_input.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.episodes_list.setEnabled(False)

        # 启动线程
        self.download_thread.start()

    def cancel_download(self):
        """取消下载"""
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.cancel()
            self.cancel_btn.setEnabled(False)
            self.log("正在取消下载...")

    def update_progress(self, current, total, percent):
        """更新下载进度"""
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"进度: {current}/{total} ({percent}%)")

    def download_finished(self, episode_name, success, message):
        """下载完成处理"""
        if success:
            self.log(f"剧集 '{episode_name}' 下载成功: {message}")
        else:
            self.log(f"剧集 '{episode_name}' 下载失败: {message}")
            QMessageBox.warning(self, "下载失败", f"'{episode_name}' 下载失败: {message}")

        # 如果所有下载都完成了
        if not self.download_thread.isRunning():
            self.log("所有下载任务已完成")
            self.fetch_btn.setEnabled(True)
            self.url_input.setEnabled(True)
            self.download_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self.episodes_list.setEnabled(True)
            self.progress_bar.setValue(100)
            self.current_episode_label.setText("当前下载: 完成")

    def clear_all(self):
        """清空所有输入"""
        self.url_input.clear()
        self.title_label.setText("视频标题: 未获取")
        self.episodes_list.clear()
        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.progress_label.setText("进度: 0/0 (0%)")
        self.current_episode_label.setText("当前下载: 无")
        self.start_index_input.clear()
        self.end_index_input.clear()
        self.log("已清空所有输入")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AnimeDownloaderApp()
    window.show()
    sys.exit(app.exec_())