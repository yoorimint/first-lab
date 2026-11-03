# first-lab.kr

사주·운세 워드프레스 블로그 (애드센스). 서버는 사주데이와 같은 iwinv 서버(Ubuntu 22.04, nginx)를 사용한다.

## 구조
| 경로 | 설명 |
|---|---|
| `server/setup.sh` | 서버 설정 적용 스크립트 (여러 번 실행해도 안전, 실패 시 자동 복구) |
| `server/nginx/` | nginx 사이트 설정, 캐시·로그인 제한, SSL |
| `server/php-fpm/` | 블로그 전용 PHP 풀 (사주데이와 격리, 메모리 절약) |

## 서버에 적용
```bash
git -C /opt/first-lab pull || git clone https://github.com/yoorimint/first-lab /opt/first-lab
bash /opt/first-lab/server/setup.sh
```

> 공개 저장소이므로 비밀번호·API 키는 절대 커밋하지 않는다.
