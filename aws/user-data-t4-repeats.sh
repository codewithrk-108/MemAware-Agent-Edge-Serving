#!/bin/bash
# User data for the R1-R7 replicate runs (docs/full500_repeats_v1.md). Runs once, as root, at first boot.
# Launch both g4dn.xlarge instances in ONE request (launch index 0 = host a, 1 = host b) from the
# AL2023 DLAMI used for R1-R7, with instance profile phase1-t4-runner (read s3://BUCKET/github-token,
# write s3://BUCKET/raw/) and shutdown behavior Stop. The token itself is never in user data.
set -uo pipefail
exec > >(tee -a /var/log/t4-repeats-bootstrap.log) 2>&1

DEADLINE=2026-11-16T18:00:00Z        # Mon 10:00 PST: no run starts unless expected to end by then
HARD_STOP='2026-11-16 21:00:00 UTC'  # Mon 13:00 PST: stop regardless
BUCKET=<bucket>
REPO_URL=https://github.com/codewithrk-108/MemAware-Agent-Edge-Serving.git   # renamed from phase-1-fastllamacpp
BASE_COMMIT=08e38a851eaa2fff1ae8ffa290f4dc6699ea3238
MODEL_SHA=9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031
SERVER_SHA_R1_R7=286ddc0b5becb98eb1cb4503ebeeac78860c1a698dad2bad5ae9d2a2d414db7c
U=ec2-user; H=/home/ec2-user; R=$H/phase1-repeats
RT=$H/t4runtime/llama-b11780-kv-src; MODEL=$H/Qwen3-0.6B-Q8_0.gguf
CRED=$H/.config/phase1-full500-v1/github.json

say() { echo "T4REPEATS $(date -u +%FT%TZ) $*"; echo "T4REPEATS $*" > /dev/console 2>/dev/null || true; }
fail() { say "FAILED: $* (stopping in 90 min; see /var/log/t4-repeats-bootstrap.log)"
         systemd-run --on-active=90min /usr/sbin/shutdown -h now; exit 1; }

IMDS=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 3600')
md() { curl -s -H "X-aws-ec2-metadata-token: $IMDS" "http://169.254.169.254/latest/$1"; }
HOST=$([ "$(md meta-data/ami-launch-index)" = 0 ] && echo a || echo b)
REGION=$(md meta-data/placement/region)
say "bootstrap host $HOST start"
systemd-run --unit=t4-hard-stop --on-calendar="$HARD_STOP" /usr/sbin/shutdown -h now

dnf install -y -q --allowerasing git cmake gcc-c++ libcurl-devel openssl-devel || say "dnf install reported errors; continuing"
# CUDA compiler: this DLAMI has no /usr/local/cuda symlink; take the newest toolkit present
find_nvcc() { NVCC=""; for c in $(ls -d /usr/local/cuda*/bin/nvcc /opt/*/bin/nvcc 2>/dev/null | sort -V); do
  [ -f "$(dirname "$(dirname "$c")")/include/cublas_v2.h" ] && NVCC=$c; done; }   # full toolkits only
find_nvcc; say "CUDA toolkits: $(ls -d /usr/local/cuda* /opt/*cuda* 2>/dev/null | tr '\n' ' ')"
if [ -z "$NVCC" ]; then
  say "nvcc not found; installing the CUDA toolkit from NVIDIA's repo"
  dnf config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/amzn2023/x86_64/cuda-amzn2023.repo \
    && dnf install -y -q cuda-toolkit-12-9 || fail "CUDA toolkit install"
  find_nvcc
fi
[ -n "$NVCC" ] || fail "nvcc not found"
CUDA_HOME=$(dirname "$(dirname "$NVCC")"); say "using $NVCC"
mkdir -p $H/bin $H/stage
md user-data > $H/stage/user-data-t4-repeats.sh
base64 -d <<'B64' | tar -xz -C $H/stage || fail "unpack driver"
H4sIAAAAAAAAA+1a63LbRpbObzxFD7KugA4JkdTFGTp0lRLLcWriy0jyTm1ULLAJNEmMQABBg5IV
WVvzDvtj32BfZN9knmS/cxpXknacqtnd2i12YgqX7tPd5/Kdcxpnvo6i437fy1SqZK7d9O6Lf3jr
o50cHfFftI2/w8HJ0fEXg+Phk5PhYDg8GnzRHxz2j4dfiP4/finbba1zmQnxRZYk+af6/db7/6PN
tu1zlUahL3Ml8qUSWt2oWMyhFgJ60culvhaXRyJbx1o454Pe+ZOOuA3zpQgDFecYFwk/iefhYp3J
PEzQS8YBqKhAu5Z1dqOyO5FVM2RqrZXmic7X8fc8UCSzvyo/1yKZ84vpvNDJ/MhLZZZ7cso0t57P
ptYiS9apFmEs0qXUanCgb5VKocViHftLGS9UIBxajBh2hZYr7DGTvuqK110hsyy8kZHuij/9s/BV
FJmlB0oGURgr3XGt79Q8yZRQ0l8SB3h5aSTjGASZM0kWqIyHFdSwiblW2IzEOH+p/Gt0lQsZxjrn
4UkWLsJYRta0IOQRa92/6iSeuuJNHN2Zbus8XedinkQ0QRDO5yrTI8sSaJnS6yjXB+DDvG2+3s3g
AJffvn79zLDoW/lh9uzg23JS3kQMNjw7sKxa7gMR6tbiRCBzSVzdMdXNwJC+l93Zw4ErLm8TsUw0
bTnKVRaDoLWU0Y0CY3UiFGtAS0VotpWSep2BN7idJaROYJCMfVVukkgKOSLd6Q8FzShkl+8Ozd3M
3B1V71zXrUfOWiNnrZGyNXJmRlqnzBxCA1JFkkM4FyGu1fsU6omlBuX6nYpPOpWxeC8G7vBYfC0G
fbEK445QcaDFjHXH6vVKhXLF6RwMKjhSqtO1SiHmMIJVOKxeKuvRO3qETvhpiOH87PT5qzN3FXRI
vyw/Wa3CnJYGDewas+z10rVedpk2ceIrXY4WswwMXhL3qYsKXJhphsW4UbJgHV6pVZLdub6+IT7c
WYlZYikaqKG+fir+6e3L04uzgXf64vLs3Dt/B1MCp6D0XYMScmOXvK3Tv1xAzkmuYX+pYBAIYRAJ
9pAajWfI4Z2zzkN7sCwybT9aBwrbyxNxcQijBGRZVrhKExJltoAQtSrviVp1rW/KS6izykNYv9Qi
yMunZHPldaLLK72epVkCRayf3FWXRKS6JiSZSf+6Wk28XmEzmCNOy0fQjwAP8H8aWNY8S1ZiKTOg
i3ZLsRQ9z88u3v10eWH6GDRzwY4YvCx7vHlz2YU6Kn+dK2KPDFq9GfvKzossxIRv3l2KsbB3IoVt
/ens7C1eO/ZK5ZIhyO6iM4BNkxLQTaZ+WSsYd3lPDGBlLu6vb7xaa+wuGyA1e5GuW2+ELYNVqDXs
x2NcNCQ71pdsRF4F/02sgk0sk3WmO10x2wQRMHsG3vgD1/WfWG/Of/zhx9enP3kXb09fey+xq/vB
SBy6/W+6YjiCgf6x3xWHI9F3T/DkiJ58c9wVx/TkEK9O+MlRVzyhi+HJg2VZgZqLOLl1OqMCdvN1
FkN/3FKdXHqLe7r+NQFT1rnfKQZCeZ2VXhRj0yyMc2du3xt6j/6l92jVexRcPno5evRq9Oji5wdx
j94P4NM8gnGOL7O1KkmZfXtmww7Bd3tFRJqeuhqAnju254PMoHM1mFRPzIP+pCRZYpqnd9Db4OXV
jvkn4rE4POn38afAPtwUtNk7APscgh+G2jZ5xmcgBl6IR2IoxmPRFyrSStzb0h4Je0baMqMraT9c
UfdJyQlSHI8cp+PPF12eqiAOVLgg/37++geAl0qNzTXNiP48BTNXKflmBsuWvhFZl8CFBVZ4eehR
emW3NM+eXNnEBHsyEinQKsNvGFeaXzSyJpdMVDtOYdriQMztXZ6Ufh9svLa3YgK7A5yQgZer97nT
6TyYxWFZRc8r8MGl1UwMi+MF3sWpC6gPkpULpkmAjIfnxDGXQqGOoZEkEbrSCh0CFsxOHRjUTI8w
eI8OGOn6yyT0lROp2KFhsEYd/qrGPLXHcMFijjC0+VA8E9WQkqQGySt6chVOXOrlhQHzMCQ/hynN
PhYy1cXkUFWYFkJNGTkDt1+sswi3PIhEFcuhuTBBp0PKtdkHaymUDMz5VWWJdqr+ZmlzXixNXGhU
uQ6YDZbSd/stm6OdQApXDJcex4H2hL0oJpBR5EeJVg6u/fVKr1eGcJcGlMsqIkVP25VZLmCsj+HT
wE9WdQMDLeup3RPps3NlYwzshUdNMOw2GBs/YQjwL25kitHKM2Elk4VDhU61gIbCAsdECQiPEVis
0lyPD4sFkJSKhxyTUHTtDOqOFAA1WJepOTzcEqxLtKvimzCDQSwU0KiIH74/P3t+9vryRwDN+dkL
2MhLu1ONZnxgAqOWZW1sv+gDDVgigC/2tWOzNeEMCyI227RZApreL/RrUIDg136JIGsEwvpgCcvT
B/eGI4TNhqcvkDSo9lJdIx0/CRTjWXvR5Avmtgm6REWu0+pTiJcWa22PE3OJeBC5zH3B7YeD8ko/
dEbiPnN1DhWkP1mYOp2r3mG/P5o0Z2GPpSPECM5hv9PUKd5QpQOzKIQapDJfQg1X4LRcgKmlWgSJ
Rwsq/Rr1IpPGtA5QR0WAyBvl5QmDCmyxxMeiK5iVwqRDhBROZ1JaHr9rWB3JRwYBi2fOvz3Sce5W
mSv3oszIdPCRpCkzpPfLOlT5hryaIvpDS0RMyMTStUL0Vna1+xazGkbCSFMwxIALi88wcoWcrwwd
jF5QROMU6Gu47JGE6WVrBnrQlkbdr3J2L9hnmVWXoRNFz2UsHwBkEB4ZG4CRRsjFbpeKMpwkTUP4
CWkS0io9KV2fcdsb8sbqLy5PL8+MBVMXG2jWeGT6bzxkG6sEFic5E29iBG3Y7B3JASapXSVCVwM7
nBtBxbgHuVCi4d3T74NxlBRIGl3C7j3OnLZR5/zs7dnp5YX305sf6iVV/YllGEEaVqpn+a6BaQFC
YZLjrpUYhrv5+9xu9XdvszBXxoEnKVxMRZc9u9OpLZS36soUvQKHxnbKTQEGCoFicTlJj+MX1qt7
+i0NvTRfJkVoBj3BffMIhsLqpAjDzNqFQ1QLQh1729ihjSxVCofKyJiVeVTFI2Um5p5mi/UK/vot
3WW0DR+IRIHT2POCxPe8LoHCiuAr8/xIaj2uBp/L2+f1gJcqSl+UXYv9ucAFTxZzOLB11kUydYpR
9BjAYcLHDsUkAIJMBQ0XsD2+1H97o39XwKWkY/vd5ffix4s3DJ9PocPNY4J1DC43TgiQokJ0lKvA
smL7Y1POw0znPYgEc+Z3qRojggezTaw2Hn5sGFj18VElpG8PKxyd9FkGNuwfvjHHDj+6viC7ozOI
Tw2SFIK6LDQaqgtcK5mJt808ibLUUCdG7I50K8wpokbH/pmE9jVcVr9vFxZRHFcU7rrAF4Z3vUxu
e/46y7BeBMjwfIC50vOVpi1dxi2OyGCuZrxrBEfG49jlqYrdsHDk+mT/Ob2drzVZGoTKlDCHDmFv
sjqOeSxK1GtCHLaX3REw1VQNJK5TYginRWPpNmF2XBocTTQ2Kzc0iwPOMefzxeY+CpX0wl1dByF8
sSTm6EKTGdG85LphCGBEvtbbYGYW1gDWAoRVZGJ32yigTTkLHxCwntjMVw/5L93ABspLMw1HWNf0
a+KYxkGBaXa+xEYXS/gqkzt4K47GyC2nkSIl9NI/HntMKVA5qNiTJtPNPFVkUbOecbJ4y/Br34JG
rG5J/8a23aE8cd6O17Btg9qZM+8UV8jbDRMK9i2TLDfeACt1Gvm0z+mYCXwWFPjUiMTPfHpmxHo1
tzfPtO8XD5QOmKAh1nTUQ/EUIrskI/73rSoWp/y5isOly6BCxzuwWpeggq43YnI+8BzXGXqphZSj
1+EQrQ295vY9FOuBDpTv8W/UHwYPrYS19lu0rfni0xszYyZtTsOmWpps5jYpHrGx1RmidmgA8uT6
wKqzQ+aVHBOkjfG6TSVTFJmFnCg7FV71zElPx82THLkZGJ/EQYlqjQXUo7+tdGB74sJjl8Tva8R7
AAUOVJ9WgdhGImDWyIFg/1OzN1Wu4NYOFpiV6OswFffMXCQOZfeHEexAwaz/9X4XMfCZjnhG7mD+
IIBN9hZxlr19X6+qNQL59DzfsbmdUinM+BMHPNu7om6wPg2fAj9BSlhv7XPYe7gjSWMc28Eqe0sT
dsD8J3doKI+NnrU1MrmlwIqRlT9blPA64v0XIDuqjKIFtyOSAN+OHr24fHT584P90KKeZ3fbKyQo
KQ6SHTozh36qlC6I7YiceP9j/t3BvuS29GUGWMcG3OFf9BVdTrpFvkr3BeJPNiG/1XbB/xiPEAeD
xE7ngEmOOp8kuuk7aoJbXmVC56M7VXUXBDe7qPc+fcE54z90Lg13orYZXn0scPko2MMwZ3s+CmM+
0383W4mKvETOQDrNrKOe2wRgZvLPkitTtCnKJJc7hqpR5Okgd/E8UkXPA4jcwzyuRsN+f/JZHPx6
LAab+n9VxQwT43mKc/JCo1vdtz26/E2PzkuBV38e+vlfCs/eNVENbUOPC99eO3v8a++GXA6JXxPo
B81DXWMm7iJKZo79+KDhm7ZAg4lsLy0N3Ocyly8yrMW5ahwdr1qnv1e2Xq9WMrsDlwjuVuRxmeSE
HJeHHTo7DWLbwZaUio8zYRyo95sHW9Sq7Nuo2DXPe03z0sejCeKLqy3l6/72fEZ8k00A/vzcvcHT
35nDl213Ll+Gv7uy+ebI35HVt1i5nd1X25czFbHyn9+3v7hUHnnDELZy/XNELVnQyPUpbnug06F7
po5Ev/ZonfoQoNg0HSPCFL8ywvlq8rAVpptWnRA38pSymY+/Hz3zrb4Zw0rFl0K5C5e/2SKKvTXf
e/kr75avJao7vFj7MJh7bRwFF8fhW0qzA5eejcVwd6hh57dJcwQfAZhRnxNnHDUP9+grGeYv4ZNO
i23PowDK82wzfZWDmiPEjvXfWP+z/VnYXQX/4Dk+Xf/VPz4+HmzWfx0NT/b1X/8T7UtRlQFVX+B/
owTs73/7t/MnHct6u87SRKtRWctDHXp5wtUrNzIL5SyMwvyOqF7KGfD5kA7GMkVfYylZKCiJx4+r
Wq3Hj0dcpWVt14AdzKabhWZwQtONsq9ps8rLcqZygSiKb/irq0ck6RvokB10NO0U/asSseRjdV10
jmWquqrCN9e6pPNzcy5rahp+V4XYZ1aFFZVEdUWNa1kfBP6zPvR6Pf6H+5cyC25peR/EUPznv4vF
URC77yOZLZRwLpWOJGRYbvf0p2F/eCieU73IT0pmLI/vwElx+upHxO6rMOI6llJENMH5OuYSmg8i
iuRKun6aitlg8OSbPoKAaWbeHvC7nnneu77pJb6/TuEu7sgt+0vI5/t3z0/BC2RsT467YrYOo7z8
FMFrI/fdmPspv5mBOdndV1pcvDztDY9PqIip+JIf1J/yy+Wa3qQfXLlXlEHBVUCvuZCJwwXa1Ksk
gNP9IP58q+LDXt89+U78+Ruv363mKUv4ChnI2NQ00djnRvAfKiWcb9XSTrsm7VN8JjyVt/pgrVXW
o/K6Xn7UK3vq5ZRJvmnV331oePIBfOr0E9V4clqa5iGE/KmeM+p5ZIwYk1pffiku6EBiHSmLFTrf
LugTzYI+Yunvq+f7UIMMNvWSK/vKi1lTjwtdHuKlqdSjTSGchGViX+XTGW0AT07w7wn+HXeYeYf1
+2o4PT9q3FYd8Pzvf/sP0foFpJluubwmW59RZHvsPhHmFLkYWj4+FEtXfH7RoLVRNAgT/XjVYOvD
nHBegamDE/E6uRGwW2x7QOfk4u3FZedpcQqVWQzPK3mH1YRA7jn98EfB6nSGj+JNESKQApOxKKvq
PgpkAPBn311wUWKHdqflXAHCaWRmmQ4hm+vgsFgB6C9AjL6DuKxMb/iLo2VNp1Pr05WqlvnW6Emv
Nk2KuLvm8WzjMcVQBcqs2GxhdUtSyh/evusWMNwlAKvplofYFcXywUagloDLdAqTwp7pRMdItEsi
KXMV+hxUyMOE5bqepk4WqonqR41pWt9kLWEqVEv/tqtIdyPcZXhs1IdyjdNWbWib8OyAqltJGNZZ
CYam/lOzoia3cRXQN2CjV8irhKjezYC/tPXKYmxzNwPCcdht1d5JEIgURZwiSOKvcgExZpFMGT8Y
HcyMinCDHJQfKQnjccW0rkjleaxpXUM4Ze/Lp4RV3auzVS7bYYdxxwWsYlcBq6nyhm+4OITC/hDm
L9cwap+yCCKMNXFNJVAguTafywnWqIaTqvhuCMNMwTrRkEQGTgw+gu9pNp8qFpUFkgnUVThTfTg6
OPjWdHp2sIC3Ws96TB3cu7g4610cPhWAHoiXkqeL3HiaQFwoP6NI4ZWMEchkvP9ZlBiHdGfRZMUs
cG/EudBXfPiYJZFIEyDuXceIo+IA3qjSYbEH4tK4KQMHJa4GxPKlzK1il7QQTnbFOqZQZopU7WBq
yBoWAf6pQ45LZG81m1gm8HcFUay49JRQqsTD3gJT1+XRl1ZIHQ6zKMGPFEmX6xXyOsaipEjvLqMu
q4etqnrYFA4T7m0qlWgqVddU6idpaL7fbkiLNsuabirpp651rm6wa57UbL9esQnlCrbxIouqDEDj
/3agv2/7tm/7tm/7tm/7tm/7tm/7tm/7tm/7tm/7tm/7tm/7tm/7tm/7tm//79t/AZPkyVMAUAAA
B64

cat > $H/bin/refresh-github-credential <<EOF
#!/bin/bash
# Copies the token from S3 into a private file read by phase1/repo_credential_helper.py.
set -euo pipefail; umask 077; mkdir -p "\$(dirname $CRED)"
aws s3 cp --region $REGION s3://$BUCKET/github-token - \\
  | python3 -c 'import json,sys; print(json.dumps({"username": "x-access-token", "password": sys.stdin.read().strip()}))' > $CRED.tmp
mv $CRED.tmp $CRED
EOF

cat > $H/stage/setup.sh <<EOF
set -euxo pipefail
export PATH=$CUDA_HOME/bin:\$HOME/.local/bin:\$PATH CUDAToolkit_ROOT=$CUDA_HOME
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone -q $REPO_URL $R
cd $R
git checkout -q -b results/t4-full500-repeats-v1-host-$HOST $BASE_COMMIT
git config user.name "T4 replicate runner (host $HOST)"
git config user.email t4-runner@example.invalid
git config credential.useHttpPath true
# the repo's helper only answers for the pre-rename name; use a copy that answers for the current one
mkdir -p $H/.local/libexec
sed "s#^REPOSITORY = .*#REPOSITORY = 'codewithrk-108/MemAware-Agent-Edge-Serving'#" phase1/repo_credential_helper.py > $H/.local/libexec/phase1_credential_helper.py
git config credential.helper "!$R/.venv/bin/python $H/.local/libexec/phase1_credential_helper.py"
cp $H/stage/full500_repeats.py phase1/; cp $H/stage/full500_repeats_v1.md docs/
sed "s/$BUCKET/<bucket>/g" $H/stage/user-data-t4-repeats.sh > aws/user-data-t4-repeats.sh
uv python install 3.12.14
uv sync --python 3.12.14
curl -fsSL --retry 5 -o $MODEL https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf
echo "$MODEL_SHA  $MODEL" | sha256sum -c -
mkdir -p $H/t4runtime
git clone -q --depth 1 --branch b11780 https://github.com/ggml-org/llama.cpp.git $RT
case \$(git -C $RT rev-parse HEAD) in 1242be66f*) ;; *) echo "wrong llama.cpp commit"; exit 1 ;; esac
git -C $RT apply $R/runtime/llama-b11780-kv-occupancy.patch
cmake -S $RT -B $RT/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75 -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DCMAKE_CUDA_COMPILER=$NVCC
cmake --build $RT/build --target llama-server -j 4
EOF
say "building runtime (about 30-40 min)"
runuser -u $U -- bash $H/stage/setup.sh > $H/stage/setup.log 2>&1 || { tail -40 $H/stage/setup.log; tail -15 $H/stage/setup.log > /dev/console; fail "setup"; }
chown -R $U:$U $H/bin $H/stage; chmod 700 $H/bin/refresh-github-credential

OUT=$R/results/t4_full500_repeats_v1; mkdir -p $OUT; P=$OUT/host_${HOST}_provenance.txt
SRV=$RT/build/bin/llama-server
{
  echo "started_utc=$(date -u +%FT%TZ)"; echo "host=<t4-host-$HOST>"
  echo "instance_type=$(md meta-data/instance-type) ami=$(md meta-data/ami-id) region=$REGION"
  echo "base_commit=$BASE_COMMIT deadline=$DEADLINE"
  sha256sum $MODEL $SRV $R/data/traces/agenttrace_full_500_seed2.jsonl $R/runtime/llama-b11780-kv-occupancy.patch
  for f in $RT/build/bin/*.so*; do [ -f "$f" ] && sha256sum "$f"; done
  [ "$(sha256sum < $SRV | cut -d' ' -f1)" = "$SERVER_SHA_R1_R7" ] && echo "llama_server_matches_R1_R7=yes" || echo "llama_server_matches_R1_R7=no"
  $R/.venv/bin/python --version; gcc --version | head -1; echo "nvcc=$NVCC"; $NVCC --version | tail -2; uname -r
  nvidia-smi --query-gpu=name,uuid,driver_version,memory.total,persistence_mode,clocks.max.sm --format=csv
  $SRV --version
} > $P 2>&1
chown -R $U:$U $OUT
runuser -u $U -- env GIT_TERMINAL_PROMPT=0 PHASE1_GITHUB_CREDENTIAL=$CRED bash -c "cd $R && $H/bin/refresh-github-credential; git add phase1/full500_repeats.py docs/full500_repeats_v1.md aws/user-data-t4-repeats.sh $P \
  && git commit -q -m 'Add R1-R7 replicate driver and host $HOST provenance' \
  && git push -q origin HEAD:refs/heads/results/t4-full500-repeats-v1-host-$HOST" \
  && say "first push OK" || say "first push FAILED (runs continue; pushes retried after each run)"

cat > /usr/local/bin/t4-repeats-run <<EOF
#!/bin/bash
cd $R
runuser -u $U -- env HOME=$H GIT_TERMINAL_PROMPT=0 MODEL=$MODEL LLAMA_SERVER=$SRV PHASE1_GITHUB_CREDENTIAL=$CRED \
  PHASE1_CREDENTIAL_REFRESH=$H/bin/refresh-github-credential REPEATS_LOG=$H/t4_repeats_driver.log \
  PHASE1_AFTER_RUN="aws s3 sync --region $REGION --only-show-errors $OUT s3://$BUCKET/raw/host-$HOST/" \
  $R/.venv/bin/python -u -m phase1.full500_repeats --host $HOST --deadline $DEADLINE --push >> $H/t4_repeats_driver.log 2>&1
echo "T4REPEATS driver exited \$?; stopping" > /dev/console
rm -f $CRED; sync; /usr/sbin/shutdown -h now
EOF
chmod 755 /usr/local/bin/t4-repeats-run
systemd-run --unit=t4-repeats /usr/local/bin/t4-repeats-run && say "driver started" || fail "driver start"
