#!/usr/bin/env python3

import os
import subprocess
import curses
import re
import time
from curses import wrapper, curs_set, color_pair, init_pair, A_BOLD, A_REVERSE
from threading import Thread, Lock
from queue import Queue

# Configuration
PDF_DIRECTORY = "/Users/stewart/BYTE"
PDFGREP_CMD = "pdfgrep"
PDFGREP_ARGS = ["-nHi"]  # -n: line numbers, -H: show filename, -i: case insensitive
PDF_VIEWER = "evince"  # Using Evince (GNOME Document Viewer)
BATCH_SIZE = 10
MAX_RESULTS = 500
MAX_FILENAME_LENGTH = 18  # Truncate filenames longer than this

class PDFSearch:
    def __init__(self):
        self.results = []
        self.lock = Lock()
        self.queue = Queue()
        self.stop_event = False
        self.processed_files = 0
        self.total_files = 0
        self.search_start = 0
        self.file_paths = {}  # Store full paths for opening
        self.evince_installed = self.check_evince_installed()

    def check_evince_installed(self):
        """Check if Evince is installed"""
        try:
            subprocess.run(["which", "evince"], 
                         check=True, 
                         stdout=subprocess.PIPE, 
                         stderr=subprocess.PIPE)
            return True
        except subprocess.CalledProcessError:
            return False

    def truncate_filename(self, filename):
        """Truncate filename if too long"""
        if len(filename) > MAX_FILENAME_LENGTH:
            return filename[:MAX_FILENAME_LENGTH-3] + "..."
        return filename

    def get_pdf_files(self):
        """Get list of PDF files with full paths"""
        try:
            files = [os.path.join(PDF_DIRECTORY, f) 
                    for f in os.listdir(PDF_DIRECTORY) 
                    if f.lower().endswith('.pdf')]
            self.total_files = len(files)
            
            # Create mapping of basename to full path
            self.file_paths = {os.path.basename(f): f for f in files}
            return files
        except Exception as e:
            print(f"Error getting PDF files: {e}")
            return []

    def search_worker(self, search_term, files):
        """Worker thread to process batches of files"""
        for i in range(0, len(files), BATCH_SIZE):
            if self.stop_event:
                break
            
            batch = files[i:i+BATCH_SIZE]
            try:
                cmd = [PDFGREP_CMD] + PDFGREP_ARGS + [search_term] + batch
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0 and result.stdout:
                    self.process_results(result.stdout, search_term)
                
                with self.lock:
                    self.processed_files += len(batch)
            except Exception as e:
                print(f"Search error: {e}")
                continue

    def process_results(self, raw_output, search_term):
        """Parse and store results safely"""
        pattern = re.compile(r'^(.*?\.pdf):(\d+):(.*)$', re.IGNORECASE)
        search_re = re.compile(f'({re.escape(search_term)})', re.IGNORECASE)
        
        for line in raw_output.splitlines():
            match = pattern.match(line)
            if match and len(self.results) < MAX_RESULTS:
                filename = os.path.basename(match.group(1))
                line_num = int(match.group(2))
                text = match.group(3).strip()
                
                parts = []
                last_end = 0
                for m in search_re.finditer(text):
                    parts.append((text[last_end:m.start()], False))
                    parts.append((text[m.start():m.end()], True))
                    last_end = m.end()
                parts.append((text[last_end:], False))
                
                with self.lock:
                    self.results.append({
                        'filename': filename,
                        'line_num': line_num,
                        'parts': parts,
                        'full_path': self.file_paths.get(filename)
                    })
                    self.queue.put(True)  # Notify new result

    def open_result(self, result_index):
        """Open the PDF at the specific search result (in background)"""
        if result_index < 0 or result_index >= len(self.results):
            return False, "Invalid selection"
        
        result = self.results[result_index]
        if not result['full_path']:
            return False, "No file path found"
        if not os.path.exists(result['full_path']):
            return False, f"File not found: {result['full_path']}"
        
        try:
            if PDF_VIEWER == "evince" and self.evince_installed:
                # Open Evince in background with page number
                subprocess.Popen([
                    "evince",
                    "-p", str(result['line_num']),  # Page number
                    result['full_path']
                ], start_new_session=True)
                return True, f"Opened in Evince (page {result['line_num']})"
            else:
                # Fall back to default open command in background
                subprocess.Popen(["xdg-open", result['full_path']], start_new_session=True)
                return True, "Opened with default viewer"
        except Exception as e:
            return False, f"Error opening PDF: {e}"

    def start_search(self, search_term):
        """Start the search process"""
        self.results = []
        self.processed_files = 0
        self.stop_event = False
        self.search_start = time.time()
        
        files = self.get_pdf_files()
        if not files:
            return False
        
        Thread(target=self.search_worker, args=(search_term, files), daemon=True).start()
        return True

    def stop_search(self):
        """Stop the search process"""
        self.stop_event = True

def display_interface(stdscr, searcher):
    """Main display interface"""
    curses.curs_set(1)
    stdscr.keypad(True)
    stdscr.timeout(100)  # Non-blocking getch
    
    # Initialize colors
    curses.start_color()
    init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
    init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    init_pair(3, curses.COLOR_CYAN, curses.COLOR_BLACK)
    init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)
    init_pair(5, curses.COLOR_BLACK, curses.COLOR_WHITE)  # For search bar highlight
    
    while True:  # Main loop to return to search prompt
        # Get search term - in this mode Q is just a character
        search_term = ""
        in_search_mode = True
        
        while in_search_mode:
            stdscr.clear()
            height, width = stdscr.getmaxyx()
            
            # Highlighted search bar
            safe_addstr(stdscr, 0, 0, " " * width, color_pair(5))
            safe_addstr(stdscr, 0, 2, "Search:", color_pair(5) | A_BOLD)
            safe_addstr(stdscr, 0, 10, search_term, color_pair(5))
            
            # Show mode and instructions
            safe_addstr(stdscr, 1, 0, "SEARCH MODE", curses.color_pair(3))
            safe_addstr(stdscr, 2, 0, "Type your search, ENTER to search, ESC to cancel", curses.color_pair(3))
            
            stdscr.refresh()
            
            key = stdscr.getch()
            if key == 10:  # Enter
                break
            elif key == 27:  # ESC - cancel input
                search_term = ""
                continue
            elif key in (curses.KEY_BACKSPACE, 127):
                search_term = search_term[:-1]
            elif 32 <= key <= 126:  # All printable characters including Q
                search_term += chr(key)
        
        if not search_term:
            continue
        
        # Start search and show results - in this mode Q quits
        searcher.stop_search()
        if not searcher.start_search(search_term):
            safe_addstr(stdscr, 3, 0, "No PDF files found!", curses.color_pair(4) | A_BOLD)
            stdscr.getch()
            continue
        
        # Results navigation mode
        current_line = 0
        last_update = 0
        in_results_mode = True
        height, width = stdscr.getmaxyx()
        visible_lines = height - 5
        
        while in_results_mode:
            now = time.time()
            
            # Update display periodically
            if now - last_update > 0.1 or not searcher.queue.empty():
                display_results(stdscr, searcher, search_term, current_line, visible_lines)
                last_update = now
                while not searcher.queue.empty():
                    searcher.queue.get()
            
            # Handle input
            key = stdscr.getch()
            if key != -1:
                if key == 27:  # ESC - return to search prompt
                    in_results_mode = False
                elif key == ord('q') or key == ord('Q'):  # Q - quit entirely
                    searcher.stop_search()
                    return
                elif key == ord('h'):  # 'h' for home (first result)
                    current_line = 0
                elif key == ord('e'):  # 'e' for end (last result)
                    current_line = len(searcher.results) - 1
                elif key == curses.KEY_UP and current_line > 0:
                    current_line -= 1
                elif key == curses.KEY_DOWN and current_line < len(searcher.results) - 1:
                    current_line += 1
                elif key == curses.KEY_PPAGE:  # Page Up
                    current_line = max(0, current_line - visible_lines)
                elif key == curses.KEY_NPAGE:  # Page Down
                    current_line = min(len(searcher.results) - 1, current_line + visible_lines)
                elif key == 10:  # ENTER - open selected result
                    if searcher.results and 0 <= current_line < len(searcher.results):
                        success, message = searcher.open_result(current_line)
                        if not success:
                            # Show error message briefly
                            safe_addstr(stdscr, 0, 0, message.ljust(width), curses.color_pair(4) | A_BOLD)
                            stdscr.refresh()
                            time.sleep(2)

def display_results(stdscr, searcher, search_term, current_line, visible_lines):
    """Update the results display with truncated filenames"""
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    
    # Highlighted search bar at top
    safe_addstr(stdscr, 0, 0, " " * width, color_pair(5))
    safe_addstr(stdscr, 0, 2, f"Search: {search_term}", color_pair(5) | A_BOLD)
    safe_addstr(stdscr, 0, width-20, "RESULTS MODE", color_pair(5))
    safe_addstr(stdscr, 0, width-15, "ESC: New Search", color_pair(5))
    
    # Status information
    status = f"Files: {searcher.processed_files}/{searcher.total_files}"
    if searcher.stop_event:
        status = f"Complete! {len(searcher.results)} matches (Page {current_line//visible_lines + 1}/{(len(searcher.results)-1)//visible_lines + 1})"
    safe_addstr(stdscr, 1, 0, status, curses.color_pair(3))
    
    # Instructions
    instructions = "↑/↓: Navigate | PgUp/PgDn: Page | h:First e:Last | ENTER: Open | Q: Quit"
    safe_addstr(stdscr, 2, 0, instructions, curses.color_pair(3))
    safe_addstr(stdscr, 3, 0, "-" * width, curses.color_pair(3))
    
    # Calculate visible range
    start_idx = max(0, current_line - current_line % visible_lines)
    end_idx = min(len(searcher.results), start_idx + visible_lines)
    
    # Display results
    for i in range(start_idx, end_idx):
        display_idx = i - start_idx + 4
        if display_idx >= height:
            break
        
        result = searcher.results[i]
        truncated_name = searcher.truncate_filename(result['filename'])
        
        # Current line indicator
        if i == current_line:
            safe_addstr(stdscr, display_idx, 0, "> ", A_REVERSE)
        
        # Filename and line number (with truncated filename)
        line_info = f"{truncated_name}:{result['line_num']}: "
        safe_addstr(stdscr, display_idx, 2, line_info, curses.color_pair(1))
        
        # Highlighted text - starts after filename + line number
        text_start_col = len(line_info) + 2
        available_width = width - text_start_col
        
        # Display as much text as we can fit
        col = text_start_col
        remaining_width = available_width
        for part, highlight in result['parts']:
            if remaining_width <= 0:
                break
            part_to_display = part[:remaining_width]
            if highlight:
                safe_addstr(stdscr, display_idx, col, part_to_display, curses.color_pair(4) | A_BOLD)
            else:
                safe_addstr(stdscr, display_idx, col, part_to_display)
            col += len(part_to_display)
            remaining_width -= len(part_to_display)
    
    # Progress bar
    if searcher.total_files > 0 and not searcher.stop_event:
        progress = min(100, int(searcher.processed_files/searcher.total_files*100))
        progress_bar = f"[{'#' * (progress//5)}{' ' * (20 - progress//5)}] {progress}%"
        safe_addstr(stdscr, height-1, 0, progress_bar, curses.color_pair(3))
    
    stdscr.refresh()

def safe_addstr(win, y, x, text, attr=0):
    """Thread-safe string display with bounds checking"""
    try:
        max_y, max_x = win.getmaxyx()
        if y < max_y and x < max_x:
            text = text[:max_x - x]
            win.addstr(y, x, text, attr)
    except curses.error:
        pass

def main():
    searcher = PDFSearch()
    try:
        wrapper(lambda stdscr: display_interface(stdscr, searcher))
    except Exception as e:
        print(f"Error: {e}")
    finally:
        searcher.stop_search()

if __name__ == "__main__":
    main()

