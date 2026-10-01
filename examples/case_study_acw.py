import sys


def build(words, n):
    line = ""
    total = 0
    for i in range(n):
        w = words[i]
        line = w + line
        total = len(w) + total
    if 0 < n:
        return line, total ** 2
    return line, 0


def count_long(words, limit):
    cnt = 0
    for w in words:
        if limit < len(w):
            cnt += 1
    return cnt


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    words = data[1:1 + n]
    line, score = build(words, n)
    print(line, score, count_long(words, 1))


main()
