import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def extract_zip_flat(zip_path, target_dir):
    """扁平化解压ZIP文件到目标目录（忽略所有嵌套层级）"""
    try:
        os.makedirs(target_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for zip_info in zip_ref.infolist():
                if '__MACOSX' in zip_info.filename:
                    continue
                if zip_info.is_dir():
                    continue

                file_name = os.path.basename(zip_info.filename)
                if not file_name:
                    continue

                target_file = os.path.join(target_dir, file_name)
                with zip_ref.open(zip_info) as src_file, open(target_file, 'wb') as dst_file:
                    shutil.copyfileobj(src_file, dst_file)

        return True
    except Exception as e:
        print(f"解压失败: {str(e)}")
        return False


def zip_folder(folder_path, zip_path):
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            for root, dirs, files in os.walk(folder_path):
                if '__MACOSX' in dirs:
                    dirs.remove('__MACOSX')
                # 强制文件按名称排序，确保遍历顺序一致
                for file in sorted(files):
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, folder_path)
                    # 创建ZipInfo对象，固定时间戳（避免受系统时间影响）
                    zip_info = zipfile.ZipInfo(arcname)
                    zip_info.date_time = (2015, 11, 28, 0, 0, 0)  # 固定为2020-01-01 00:00:00
                    zip_info.compress_type = zipfile.ZIP_DEFLATED
                    # 写入文件内容（忽略原文件元数据）
                    with open(file_path, 'rb') as f:
                        zipf.writestr(zip_info, f.read())
        print(f"成功打包: {zip_path}")
        return True
    except Exception as e:
        print(f"打包失败: {str(e)}")
        return False


def calculate_file_hash(file_path, algorithm='sha256'):
    """计算文件哈希值"""
    hash_obj = hashlib.new(algorithm)
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def cleanup_resources(keep_first_zip=False):
    """清理爬取过程中产生的资源文件"""
    if os.path.exists("./maps"):
        try:
            shutil.rmtree("./maps")
            print("已清理maps目录")
        except Exception as e:
            print(f"清理maps目录失败: {str(e)}")

    # 保留第一次爬取的压缩包
    zip_files = ["./r6maps.zip", "./hash.txt"]
    if not keep_first_zip:
        for file in zip_files:
            if os.path.exists(file):
                try:
                    os.remove(file)
                    print(f"已清理{file}")
                except Exception as e:
                    print(f"清理{file}失败: {str(e)}")


def run_crawl(zip_suffix=""):
    """执行单次爬取流程，返回(是否有错误, 压缩包哈希, 压缩包路径)"""
    # 根据操作系统选择浏览器和驱动
    if sys.platform.startswith('win32'):
        # Windows环境：使用Edge浏览器
        options = EdgeOptions()
        # 配置Edge选项
        options.add_argument('--headless=new')
        options.add_argument('--disable-gpu')
        # Windows下Edge驱动路径
        try:
            service = EdgeService(executable_path="./msedgedriver.exe")
        except Exception as e:
            print(f"Edge驱动配置异常: {e}")
            return True, None, None  # 返回错误状态
    else:
        # Linux环境（GitHub Action）：使用Chrome浏览器
        options = ChromeOptions()
        # 配置Chrome选项（适配Ubuntu环境）
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        # Ubuntu下Chrome驱动路径
        try:
            service = ChromeService(executable_path="/usr/bin/chromedriver")
        except Exception as e:
            print(f"Chrome驱动配置异常: {e}")
            return True, None, None  # 返回错误状态

    # 初始化驱动
    try:
        if sys.platform.startswith('win32'):
            driver = webdriver.Edge(service=service, options=options)
        else:
            driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        print(f"浏览器初始化失败: {e}")
        return True, None, None

    download_dir = "./maps"
    os.makedirs(download_dir, exist_ok=True)
    map_status = {}
    has_error = False
    total_maps = 0
    file_hash = None
    zip_path = f"./r6maps{zip_suffix}.zip"  # 支持自定义后缀

    try:
        # 访问地图列表页面
        driver.get("https://zh-cn.ubisoft.com/r6s/maps")
        WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "maps-list"))
                )

        # 获取地图数据
        maps_data = []
        try:
            maps_data = driver.execute_script("return maps;")
        except Exception as e:
            page_source = driver.page_source
            maps_pattern = re.compile(r'var\s+maps\s*=\s*(\[.*?\])', re.DOTALL)
            match = maps_pattern.search(page_source)
            if not match:
                raise ValueError("无法获取地图数据")

            maps_json = match.group(1).replace("'", '"')
            maps_json = re.sub(r'(\w+):', r'"\1":', maps_json)
            maps_data = json.loads(maps_json)

        # 处理每个地图
        total_maps = len(maps_data)
        for map_info in maps_data:
            map_name = map_info["name"]
            map_url = f"https://zh-cn.ubisoft.com/r6s/map?name={map_info['url']}"

            try:
                driver.get(map_url)
                time.sleep(1)

                download_button = WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located(
                                (By.XPATH, '//a[contains(@data-innertext, "download blueprints")]')
                                )
                        )

                download_url = download_button.get_attribute("href")
                if not download_url:
                    raise ValueError("下载链接为空")

                temp_zip_path = os.path.join(download_dir, f"{map_name}.zip")
                response = requests.get(download_url, stream=True, timeout=30)

                if response.status_code != 200:
                    raise Exception(f"地图压缩包{response.status_code}")

                with open(temp_zip_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                map_target_dir = os.path.join(download_dir, map_name)
                if extract_zip_flat(temp_zip_path, map_target_dir):
                    os.remove(temp_zip_path)
                    map_status[map_name] = "正常"
                else:
                    raise Exception("解压失败")

            except Exception as e:
                error_msg = str(e)
                if "404" in error_msg:
                    map_status[map_name] = f"地图压缩包404"
                elif "下载链接为空" in error_msg:
                    map_status[map_name] = "地图链接404"
                else:
                    map_status[map_name] = error_msg
                has_error = True

    except Exception as e:
        map_status["全局"] = f"爬取流程异常: {str(e)}"
        has_error = True
    finally:
        driver.quit()

    # 打包maps文件夹
    zip_result = zip_folder(download_dir, zip_path)
    if not zip_result:
        map_status["打包"] = f"{zip_path}打包失败"
        has_error = True

    # 输出日志
    print(f"\n总 {total_maps} 图")
    for name, status in map_status.items():
        print(f"{name}：{status}")

    # 计算哈希值
    if os.path.exists(zip_path):
        file_hash = calculate_file_hash(zip_path)
        # 保存哈希值到对应文件
        with open(f"./hash{zip_suffix}.txt", "w") as f:
            f.write(file_hash)
        print(f"\n文件哈希值: {file_hash}")

    return has_error, file_hash, zip_path


def main():
    # 调试模式：先清理残留资源
    cleanup_resources()

    # 第一次爬取（生成带first后缀的压缩包）
    print("===== 第一次爬取开始 =====")
    first_error, first_hash, first_zip = run_crawl("_first")

    # 如果第一次无错误，直接正常退出
    if not first_error:
        # 重命名为默认名称用于后续流程
        if os.path.exists(first_zip):
            shutil.copy2(first_zip, "./r6maps.zip")
            with open("./hash_first.txt", "r") as f:
                with open("./hash.txt", "w") as out_f:
                    out_f.write(f.read())
        sys.exit(0)

    # 第一次有错误，执行重试逻辑
    print("\n===== 检测到错误，准备重试 =====")
    cleanup_resources(keep_first_zip=True)
    time.sleep(1)  # 短暂等待

    # 第二次爬取（生成带second后缀的压缩包）
    print("\n===== 第二次爬取开始 =====")
    second_error, second_hash, second_zip = run_crawl("_second")

    # 对比两次哈希
    print("\n===== 爬取结果对比 =====")
    print(f"第一次哈希: {first_hash} (文件: {first_zip})")
    print(f"第二次哈希: {second_hash} (文件: {second_zip})")

    # 如果两次哈希一致，认为不是网络问题，正常退出
    if first_hash and second_hash and first_hash == second_hash:
        print("两次哈希一致")
        # 用第一次的结果作为最终结果
        shutil.copy2(first_zip, "./r6maps.zip")
        with open("./hash_first.txt", "r") as f:
            with open("./hash.txt", "w") as out_f:
                out_f.write(f.read())
        sys.exit(0)
    else:
        print("两次哈希不一致")
        sys.exit(1)


if __name__ == "__main__":
    main()
