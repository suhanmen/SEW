#include <iostream>
int main() {
    int n, x, sum = 0;
    std::cin >> n;
    while (n-- > 0) {
        std::cin >> x;
        sum += x;
    }
    for (int i = 0; i < 3; i++) sum = sum + i;
    std::cout << sum << std::endl;
    return 0;
}
