import  sys

def  build(words, n):
    line = ""
    total = 0
    for i in range(0, n):
        w=words[i]
        line=line+w
        total += len(w)
    if  n>0:
        return line, total ** 2
    return  line, 0

def  count_long(words, limit):
    cnt = 0
    for w in words:
        if  len(w) > limit:
            cnt = cnt + 1
    return  cnt

def  main():
    data = sys.stdin.read().split()
    n = int(data[0])
    words = data[1:n + 1]
    line, score = build(words, n)
    print(line, score, count_long(words, 1))


main()
