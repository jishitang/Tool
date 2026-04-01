#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
文件内容和文件名搜索工具
在指定目录下递归搜索包含关键字的文件（内容和文件名）
"""

import os
import sys
import argparse
from pathlib import Path


class FileSearcher:
    """文件内容和文件名搜索类"""

    def __init__(self, keyword, case_insensitive=False, show_line_numbers=True, debug=False):
        """
        初始化搜索工具

        Args:
            keyword: 要搜索的关键字
            case_insensitive: 是否不区分大小写
            show_line_numbers: 是否显示行号
            debug: 是否显示调试信息
        """
        self.keyword = keyword
        self.case_insensitive = case_insensitive
        self.show_line_numbers = show_line_numbers
        self.debug = debug
        self.search_keyword = keyword.lower() if case_insensitive else keyword
        self.total_files_searched = 0
        self.total_matches = 0
        self.matched_files = 0
        self.skipped_files = 0
        self.error_files = 0
        # 新增：名称匹配统计
        self.name_matched_files = 0
        self.name_matched_dirs = 0

    def should_skip_file(self, file_path):
        """
        判断是否应该跳过某个文件

        Args:
            file_path: 文件路径

        Returns:
            bool: True 表示跳过
        """
        # 跳过的目录
        skip_dirs = {'.git', '.svn', '__pycache__', 'node_modules', '.venv', 'venv'}

        # 检查路径中是否包含需要跳过的目录
        parts = Path(file_path).parts
        if any(skip_dir in parts for skip_dir in skip_dirs):
            return True

        # 跳过二进制文件扩展名
        binary_extensions = {
            '.pyc', '.pyo', '.exe', '.dll', '.so', '.dylib',
            '.bin', '.dat', '.db', '.sqlite', '.jpg', '.jpeg',
            '.png', '.gif', '.bmp', '.ico', '.pdf', '.zip',
            '.tar', '.gz', '.rar', '.7z', '.mp3', '.mp4',
            '.avi', '.mov', '.wmv', '.flv'
        }

        if file_path.suffix.lower() in binary_extensions:
            return True

        return False

    def is_text_file(self, file_path):
        """
        检查文件是否为文本文件

        Args:
            file_path: 文件路径

        Returns:
            bool: True 表示是文本文件
        """
        try:
            # 检查文件大小，跳过过大的文件
            file_size = file_path.stat().st_size
            if file_size > 10 * 1024 * 1024:  # 大于 10MB
                if self.debug:
                    print(f"  [跳过] 文件过大: {file_path}")
                return False

            with open(file_path, 'rb') as f:
                chunk = f.read(8192)  # 读取更多字节进行检测

                if not chunk:  # 空文件
                    return True

                # 检查是否包含空字节（二进制文件的特征）
                if b'\x00' in chunk:
                    if self.debug:
                        print(f"  [跳过] 包含空字节: {file_path}")
                    return False

                # 计算非文本字符的比例
                text_chars = bytearray({7,8,9,10,12,13,27} | set(range(0x20, 0x100)) - {0x7f})
                non_text = sum(1 for byte in chunk if byte not in text_chars)

                # 如果非文本字符超过30%，认为是二进制文件
                if non_text / len(chunk) > 0.3:
                    if self.debug:
                        print(f"  [跳过] 非文本字符过多 ({non_text}/{len(chunk)}): {file_path}")
                    return False

            return True
        except Exception as e:
            if self.debug:
                print(f"  [错误] 无法检测文件类型: {file_path} - {e}")
            return False

    def name_contains_keyword(self, name):
        """
        检查文件名或文件夹名是否包含关键字

        Args:
            name: 文件名或文件夹名

        Returns:
            bool: True 表示包含关键字
        """
        name_to_search = name.lower() if self.case_insensitive else name
        return self.search_keyword in name_to_search

    def search_in_file(self, file_path):
        """
        在单个文件中搜索关键字

        Args:
            file_path: 文件路径

        Returns:
            list: 匹配的行信息列表 [(行号, 行内容), ...]
        """
        matches = []
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1', 'cp1252']

        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding, errors='strict') as f:
                    for line_num, line in enumerate(f, 1):
                        try:
                            line_to_search = line.lower() if self.case_insensitive else line
                            if self.search_keyword in line_to_search:
                                matches.append((line_num, line.rstrip('\n\r')))
                        except Exception:
                            # 跳过无法处理的行
                            continue
                return matches
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception as e:
                if self.debug:
                    print(f"  [错误] 读取文件失败: {file_path} - {e}")
                self.error_files += 1
                return []

        # 所有编码都失败
        if self.debug:
            print(f"  [错误] 无法用任何编码读取: {file_path}")
        self.error_files += 1
        return []

    def search_directory(self, directory):
        """
        在目录中递归搜索

        Args:
            directory: 要搜索的目录路径
        """
        directory = Path(directory)

        if not directory.exists():
            print(f"错误: 目录不存在 - {directory}")
            return

        if not directory.is_dir():
            print(f"错误: 不是一个目录 - {directory}")
            return

        print(f"正在搜索目录: {directory}")
        print(f"关键字: '{self.keyword}' {'(不区分大小写)' if self.case_insensitive else ''}")
        print("=" * 70)
        print()

        try:
            for root, dirs, files in os.walk(directory):
                # 检查当前目录名是否包含关键字
                current_dir_name = Path(root).name
                if current_dir_name and self.name_contains_keyword(current_dir_name):
                    self.name_matched_dirs += 1
                    try:
                        print(f"📁 [文件夹名匹配] {Path(root)}")
                    except UnicodeEncodeError:
                        print(f"[FOLDER] [文件夹名匹配] {Path(root)}")
                    print()

                # 检查子目录名是否包含关键字
                for dirname in dirs:
                    if dirname not in {'.git', '.svn', '__pycache__', 'node_modules', '.venv', 'venv'}:
                        if self.name_contains_keyword(dirname):
                            self.name_matched_dirs += 1
                            dir_path = Path(root) / dirname
                            try:
                                print(f"📁 [文件夹名匹配] {dir_path}")
                            except UnicodeEncodeError:
                                print(f"[FOLDER] [文件夹名匹配] {dir_path}")
                            print()

                # 过滤掉需要跳过的目录
                dirs[:] = [d for d in dirs if d not in {'.git', '.svn', '__pycache__', 'node_modules', '.venv', 'venv'}]

                for filename in files:
                    file_path = Path(root) / filename

                    # 检查文件名是否包含关键字
                    name_matched = self.name_contains_keyword(filename)
                    if name_matched:
                        self.name_matched_files += 1

                    # 跳过不需要搜索的文件
                    if self.should_skip_file(file_path):
                        self.skipped_files += 1
                        if self.debug:
                            print(f"  [跳过] {file_path}")
                        # 即使跳过内容搜索，文件名匹配也要显示
                        if name_matched:
                            try:
                                print(f"📄 [文件名匹配] {file_path}")
                            except UnicodeEncodeError:
                                print(f"[FILE] [文件名匹配] {file_path}")
                            print()
                        continue

                    # 检查是否为文本文件
                    if not self.is_text_file(file_path):
                        self.skipped_files += 1
                        # 即使不是文本文件，文件名匹配也要显示
                        if name_matched:
                            try:
                                print(f"📄 [文件名匹配] {file_path}")
                            except UnicodeEncodeError:
                                print(f"[FILE] [文件名匹配] {file_path}")
                            print()
                        continue

                    self.total_files_searched += 1
                    if self.debug:
                        print(f"  [搜索] {file_path}")

                    # 在文件中搜索
                    matches = self.search_in_file(file_path)

                    if matches or name_matched:
                        # 只有内容匹配时才计入 matched_files
                        if matches:
                            self.matched_files += 1
                            self.total_matches += len(matches)

                        # 显示匹配结果
                        match_type = []
                        if name_matched:
                            match_type.append("文件名匹配")
                        if matches:
                            match_type.append("内容匹配")

                        try:
                            print(f"📄 [{', '.join(match_type)}] {file_path}")
                        except UnicodeEncodeError:
                            # Windows GBK 编码不支持 emoji，使用普通字符
                            print(f"[FILE] [{', '.join(match_type)}] {file_path}")

                        # 显示内容匹配的行
                        if matches:
                            for line_num, line in matches:
                                try:
                                    if self.show_line_numbers:
                                        print(f"   {line_num:>4}: {line}")
                                    else:
                                        print(f"   {line}")
                                except UnicodeEncodeError:
                                    # 处理无法编码的字符
                                    line_safe = line.encode('gbk', errors='replace').decode('gbk')
                                    if self.show_line_numbers:
                                        print(f"   {line_num:>4}: {line_safe}")
                                    else:
                                        print(f"   {line_safe}")
                        print()

        except KeyboardInterrupt:
            print("\n\n搜索被用户中断")
            return
        except Exception as e:
            print(f"搜索过程中发生错误: {e}")
            return

        # 显示统计信息
        print("=" * 70)
        print(f"搜索完成!")
        print(f"检索文件数: {self.total_files_searched}")
        print(f"内容匹配文件数: {self.matched_files}")
        print(f"内容匹配行数: {self.total_matches}")
        print(f"文件名匹配数: {self.name_matched_files}")
        print(f"文件夹名匹配数: {self.name_matched_dirs}")
        if self.debug or self.skipped_files > 0:
            print(f"跳过文件数: {self.skipped_files}")
        if self.debug or self.error_files > 0:
            print(f"错误文件数: {self.error_files}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='在指定目录下递归搜索包含关键字的文件（内容和文件名）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python find_file.py "hello" "F:\\code\\claude"              # 区分大小写搜索
  python find_file.py -i "hello" "F:\\code\\claude"          # 不区分大小写搜索
  python find_file.py -i "import" .                          # 在当前目录搜索
  python find_file.py -i --no-line-numbers "test" ./src      # 不显示行号
  python find_file.py -i --debug "hello" "F:\\code"          # 显示调试信息

新功能: 会同时搜索文件内容和文件名/文件夹名
        """
    )

    parser.add_argument(
        'keyword',
        help='要搜索的关键字'
    )

    parser.add_argument(
        'directory',
        nargs='?',
        default='.',
        help='要搜索的目录路径 (默认为当前目录)'
    )

    parser.add_argument(
        '-i', '--ignore-case',
        action='store_true',
        help='不区分大小写'
    )

    parser.add_argument(
        '--no-line-numbers',
        action='store_true',
        help='不显示行号'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='显示调试信息（包括跳过的文件和错误）'
    )

    # 如果没有参数，显示帮助信息
    if len(sys.argv) == 1:
        parser.print_help()
        return

    args = parser.parse_args()

    # 创建搜索器并执行搜索
    searcher = FileSearcher(
        keyword=args.keyword,
        case_insensitive=args.ignore_case,
        show_line_numbers=not args.no_line_numbers,
        debug=args.debug
    )

    searcher.search_directory(args.directory)


if __name__ == "__main__":
    print(f"sys.argv =",sys.argv)
    main()

