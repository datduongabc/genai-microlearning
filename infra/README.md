# Triển khai lên AWS

Terraform cho bản đồ án của `docs/architecture/system-design-v1.md`: một thư mục, một môi trường mỗi lần `apply`, xóa được bằng một lệnh.

**Đã chạy trên AWS** từ ngày 2026-11-11 (commit `8c2f1e8`): apply, migration, rollout, frontend và smoke test qua CloudFront đều qua. Lần apply đầu ngày 2026-11-06 dừng ở CloudFront vì tài khoản chưa được xác minh; AWS xác minh xong ngày 2026-11-10. Lần deploy đầu, worker báo `password authentication failed for user "app_worker"` cho tới khi migration tạo xong vai trò DB, rồi tự hết.

## Có gì

| Phần | Tài nguyên | Ghi chú |
| --- | --- | --- |
| Mạng | VPC 2 AZ, subnet public cho task, subnet data không có đường ra Internet, S3 gateway endpoint | Không NAT: task có IP công khai, không có rule inbound từ Internet |
| Vào | CloudFront (`/api/*` tới ALB, còn lại là frontend trong S3), ALB chỉ nhận từ CloudFront | ALB kiểm header bí mật `X-Origin-Verify`, health check `/ready` |
| Tính toán | ECS Fargate: `api`, `worker`, `llm-gateway`, `embedder`, và `reranker` khi `reranker_provider = "jina"` | Service Connect: tên `gateway`, `embedder`, `reranker` giống compose |
| Dữ liệu | RDS PostgreSQL 16 (pgvector, bắt buộc TLS), ElastiCache Valkey một node, S3 cho tệp tải lên và media | Trình duyệt gửi tệp thẳng vào S3 bằng presigned POST (CORS chỉ cho phép POST từ địa chỉ CloudFront); tệp tự hết hạn sau 7 ngày, phòng job lỗi bỏ lại |
| Bí mật | Parameter Store SecureString | Khóa nhà cung cấp chỉ tới `llm-gateway`; URL chủ DB chỉ tới task migrate |
| Scale | `api` theo request/task và CPU; `worker` theo `JobsReady`; `embedder` theo CPU | Worker có job đang chạy được bảo vệ khỏi scale-in |
| Giám sát | Log JSON 14 ngày, cảnh báo qua SNS, AWS Budgets $50/$100/$150 | Cảnh báo Jev chỉ có khi `reranker_provider = "jev"` |

## Lần đầu

Cần: AWS CLI đã đăng nhập, Docker, Node, `jq`, Terraform ≥ 1.10.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # sửa alarm_email
# Tag image sẽ đẩy ở bước 1. State mới chưa giữ tag nào, nên bước 1 và 2 truyền nó qua IMAGE_TAG;
# từ đó tag nằm trong state (output image_tag) và deploy.sh cập nhật nó mỗi lần rollout.
TAG=$(git rev-parse --short=7 HEAD)

# 0. Bucket giữ state, một lần cho mỗi tài khoản. State chứa mật khẩu DB sinh ra, nên bucket riêng
#    tư, mã hóa, chỉ nhận TLS, và có versioning để lấy lại bản cũ khi state hỏng.
B="microlearning-tfstate-$(aws sts get-caller-identity --query Account --output text)"
aws s3api create-bucket --bucket "$B" --region ap-southeast-1 --create-bucket-configuration LocationConstraint=ap-southeast-1
aws s3api put-bucket-versioning --bucket "$B" --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$B" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
cp backend.hcl.example backend.hcl              # điền tên bucket
terraform init -backend-config=backend.hcl

# 1. Kho ECR trước, để có chỗ đẩy image.
IMAGE_TAG=$TAG ../scripts/tf.sh apply -target=aws_ecr_repository.repo -target=aws_ecr_lifecycle_policy.keep_recent
IMAGES_ONLY=1 ../scripts/deploy.sh             # thêm WITH_RERANKER=1 nếu dùng jina

# 2. Phần còn lại. Service khởi động trước migration, nên api chưa qua /ready và lần deploy đầu có
#    thể bị circuit breaker đánh dấu thất bại; bước 4 khởi động lại service đó sau migration.
#    Topic SNS mới gửi email xác nhận tới alarm_email: bấm Confirm, mỗi lần tạo lại stack.
IMAGE_TAG=$TAG ../scripts/tf.sh apply

# 3. Khóa nhà cung cấp. Terraform không bao giờ thấy chúng và không ghi đè sau lần tạo.
PREFIX=$(terraform output -raw ssm_prefix)
aws ssm put-parameter --overwrite --type SecureString --name "$PREFIX/openrouter-api-key" --value "..."
#   tương tự: typesafe-api-key, moodle-token
aws ecs update-service --cluster "$(terraform output -raw cluster)" --service gateway --force-new-deployment

# 4. Migration, frontend, smoke test.
SKIP_BUILD=1 ../scripts/deploy.sh
```

## Mỗi lần deploy

```bash
infra/scripts/deploy.sh
```

Chỉ commit đã nằm trên `origin/main` (đã qua review và CI) mới được deploy; commit khác thì script dừng trước khi build.
Ở bước rolling update, Terraform in plan và chờ gõ `yes`: đọc plan trước, nhất là khi có tài nguyên bị thay thế (RDS thay thế là một database mới, rỗng).

Thứ tự: build và đẩy image theo commit → chạy migration bằng image mới trong một task riêng → rolling update → frontend → gọi `/api/ready` qua CloudFront. Migration lỗi thì dừng, chưa service nào đổi. Task mới không qua health check thì circuit breaker của ECS tự quay về bản cũ.

Trong lúc rolling update, bản cũ và mới chạy cùng lúc trên schema mới, nên thay đổi phá vỡ tương thích phải tách hai lần deploy: thêm trước, bỏ sau.

Tag đang chạy nằm trong state của môi trường (output `image_tag`), và `tf.sh` đọc nó rồi truyền lại cho mọi `plan` và `apply`, nên một lệnh từ bất kỳ bản checkout nào cũng giữ đúng image của lần deploy cuối.
`deploy.sh` đặt `IMAGE_TAG` thành commit đang rollout cho lần apply của nó; `IMAGE_TAG` luôn thắng tag trong state.
Vì vậy mọi thay đổi hạ tầng khác chạy qua `infra/scripts/tf.sh apply`, không chạy `terraform apply` trực tiếp: tag cũ để trong `terraform.tfvars` sẽ đưa mọi service về image cũ.
State chưa có output đó (stack mới, hoặc lần apply đầu sau khi output này được thêm) thì `tf.sh` dừng: chạy `deploy.sh`, hoặc đặt tag đang chạy cho đúng một lệnh (`IMAGE_TAG=<commit> infra/scripts/tf.sh apply`, tag lấy từ `aws ecs describe-services`).
Đừng `export IMAGE_TAG`: biến còn lại trong shell sẽ thắng tag trong state ở lần sau và đưa service về image cũ.

## Staging

Staging là một bản sao của stack ở cỡ nhỏ nhất vẫn chạy đủ mọi service: api và worker tối đa 2 task, `db.t4g.small`, ngân sách riêng $30 một tháng (`infra/environments/staging.tfvars`).
Một commit được deploy lên staging và kiểm ở đó trước, rồi mới đưa sang demo.

`infra/scripts/tf.sh` chọn môi trường theo biến `ENVIRONMENT` (mặc định `demo`, chạy y như trước).
Môi trường khác demo có state riêng ở key `<môi trường>/terraform.tfstate` trong cùng bucket, thư mục `.terraform-<môi trường>` riêng, và đọc biến của nó sau `terraform.tfvars`.
Vì vậy một lệnh của staging không bao giờ đọc hay ghi vào state của demo.

Dựng lần đầu, giống mục "Lần đầu" nhưng qua `tf.sh`:

```bash
export ENVIRONMENT=staging
TAG=$(git rev-parse --short=7 HEAD)
IMAGE_TAG=$TAG infra/scripts/tf.sh apply -target=aws_ecr_repository.repo -target=aws_ecr_lifecycle_policy.keep_recent
IMAGES_ONLY=1 infra/scripts/deploy.sh
IMAGE_TAG=$TAG infra/scripts/tf.sh apply
PREFIX=$(infra/scripts/tf.sh output -raw ssm_prefix)   # /microlearning-staging, khác của demo
aws ssm put-parameter --overwrite --type SecureString --name "$PREFIX/openrouter-api-key" --value "..."
#   tương tự: typesafe-api-key
aws ecs update-service --cluster "$(infra/scripts/tf.sh output -raw cluster)" --service gateway --force-new-deployment
SKIP_BUILD=1 infra/scripts/deploy.sh
```

Mỗi lần phát hành:

```bash
ENVIRONMENT=staging infra/scripts/deploy.sh     # build, migrate, roll out, smoke test trên staging
# kiểm các luồng chính trên địa chỉ staging (output url): tải tài liệu, sinh module, chat, chấm mù, LTI
PROMOTE_FROM=staging infra/scripts/deploy.sh    # đưa đúng image đó sang demo, không build lại
```

`PROMOTE_FROM` đẩy image của commit từ kho ECR của staging sang kho của demo và so digest, nên demo chạy đúng những byte đã kiểm trên staging.
Build lại thì sẽ cài bản Debian mới nhất lúc build và ra image khác.
Commit chưa từng chạy trên staging thì lệnh dừng trước khi đổi gì.

Staging cũng tốn tiền theo giờ khi đang chạy. Kiểm xong thì xóa:

```bash
ENVIRONMENT=staging infra/scripts/tf.sh destroy
```

## Video và flashcard

- **Kho `media/`.** Hình, giọng và video do worker sinh nằm trong cùng bucket uploads, dưới tiền tố `media/` (`media_s3_prefix`).
  Tiền tố `uploads/` tự hết hạn sau 7 ngày, còn `media/` thì không: tệp ở lại cùng tài liệu và bị xóa khi tài liệu bị xóa.
  `api` có quyền `PutObject`, `GetObject`, `DeleteObject` trên `media/*` để ký link phát video và xóa tệp, `worker` có cùng ba quyền để ghi và đọc lại khi dựng (`iam.tf`, statement `Media`).
- **Chromium trong image backend.** Slide của video được dựng bằng Chromium (headless shell của Playwright, cài ở `backend/Dockerfile` cùng `ffmpeg`).
  Worker dựng slide khi sinh video, `api` dùng nó để xem trước slide, nên cả hai chạy image `backend` này và không cần image riêng.
  Nhớ rằng `PLAYWRIGHT_VERSION` phải bằng bản trong `uv.lock` (`tests/unit/test_backend_image.py` kiểm).
- **Model giọng và hình.** Danh sách nằm trong `backend/app/llm/media.py`: giọng `voice-heygen` (mặc định), `voice-gemini-lite`, `voice-gemini-flash`; hình `image-nano-banana` (mặc định), `image-qwen`.
  Giảng viên chọn trong hộp sinh.
  Cả hai đi qua OpenRouter bằng khóa `openrouter-api-key` đã có trong SSM, và khóa đó vẫn chỉ tới `llm-gateway`: `devops/gateway/config.yaml` chuyển đúng ba đường `/openrouter/audio/speech`, `/openrouter/chat/completions`, `/openrouter/images`.
  Không có biến Terraform riêng cho nhà cung cấp giọng hay hình; đổi model là sửa `media.py`.
- **Hạn mức và chi phí.** Mỗi lần vẽ hình khoảng $0,037 (Nano Banana) hoặc $0,018 (Qwen), mỗi lần dựng tốn $0,03 đến $0,11 tiền giọng.
  `VIDEO_REDRAWS_PER_DAY` (mặc định 20) và `VIDEO_RENDERS_PER_DAY` (mặc định 10) giới hạn hai việc này, còn sinh video hoặc bộ thẻ tính vào hạn mức sinh hiện có.
  Số đo thật: [video thử](../docs/measurements/2026-11-12-video-trial.md).

## Moodle cho bản AWS

Moodle không nằm trong stack. Để thử LTI với một Moodle thật mà không tốn thêm tiền, chạy Moodle của kho mã trên máy mình và đưa nó ra Internet qua Cloudflare quick tunnel (không cần tài khoản Cloudflare):

```bash
bash infra/scripts/moodle-tunnel.sh    # sau deploy.sh; Ctrl-C để dừng
```

Script dựng Moodle 5.2 dùng một lần (Postgres và moodledata trên tmpfs, không đụng stack compose), tạo khóa học, một giảng viên, một sinh viên và công cụ LTI trỏ tới output `url`, đăng ký Moodle này vào database trên AWS bằng một task ECS, rồi in địa chỉ và mật khẩu. Mở khóa học bằng tài khoản giảng viên trước, sau đó tới sinh viên.

Giới hạn:

- **Địa chỉ đổi mỗi lần chạy,** và issuer LTI đổi theo: tài khoản và khóa học tạo qua lần chạy trước không được nhận lại. Giữ script chạy suốt buổi demo; máy ngủ hoặc mất mạng là mất Moodle đó.
- **Chỉ có LTI** (launch và danh sách lớp). Xuất Book và Quiz sang Moodle cần thêm `moodle_base_url`, `moodle_wwwroot` bằng địa chỉ tunnel và `moodle-token` trong SSM, tức một lần `infra/scripts/tf.sh apply` nữa cho mỗi địa chỉ.
- Lưu lượng đi qua Cloudflare, nên chỉ dùng với dữ liệu thử.

Cần một địa chỉ ổn định thì thay bằng một EC2 nhỏ chạy Moodle (khoảng $0,02 đến $0,03 một giờ), chưa có trong Terraform này.

## Tạm dừng khi không dùng

```bash
infra/scripts/stack-power.sh down     # mọi service ECS về 0 task, ngưng autoscaling, dừng RDS
infra/scripts/stack-power.sh up       # bật RDS, đưa service về mức tối thiểu, gọi /api/ready
infra/scripts/stack-power.sh status   # task đang chạy/mong muốn của từng service, trạng thái RDS
```

Chạy `down` sau mỗi buổi demo hay đo, khi vài giờ tới không ai dùng.
Lúc chạy, stack tốn khoảng $14,4 một ngày, trong đó Fargate khoảng $9,7 và RDS khoảng $2,4.
Lúc dừng, còn tính tiền ALB, Valkey, IPv4 công khai của ALB, ổ đĩa RDS và S3: ước tính khoảng $1 đến $1,5 một ngày, chưa đo trên hóa đơn.
`up` mất khoảng 10 đến 15 phút, phần lớn là chờ RDS khởi động.
`ENVIRONMENT=staging` dùng được như với `tf.sh`.

RDS tự bật lại sau 7 ngày dừng và tính tiền từ lúc đó, nên trong vòng một tuần phải chạy `down` lại.
`down` trên stack đã về 0 task chỉ dừng lại database.

Trong lúc dừng, chạy `up` trước mọi `tf.sh apply` hay `deploy.sh`.
`deploy.sh` tự dừng khi RDS không ở trạng thái `available`, vì migration và smoke test cần database.
`tf.sh apply` không bật task và không bật lại autoscaling (service bỏ qua `desired_count`, scalable target bỏ qua `suspended_state`), nhưng AWS không cho sửa một RDS đang dừng, và đổi `min`/`max` của scalable target có thể bật task của service đó khi chưa có database.
Script đọc cluster, database và số task từ output của Terraform (`cluster`, `db_instance`, `service_counts`); state cũ chưa có hai output sau thì chạy `tf.sh apply` một lần.

## Xóa

```bash
infra/scripts/tf.sh destroy
```

Còn lại sau khi xóa: snapshot cuối của RDS (`microlearning-<environment>-final-<8 ký tự hex>`, tên mới mỗi lần nên không đụng snapshot của lần trước). Muốn xóa hẳn: `aws rds delete-db-snapshot --db-snapshot-identifier <tên>`.

Kho ECR cũng bị xóa cùng image, nên lần dựng sau bắt đầu lại từ bước 1 và đẩy lại khoảng 2.5 GB image.

## Quyết định và giới hạn

- **Worker không về 0.** Chính worker báo độ sâu hàng đợi. Không còn worker nào thì không có metric, và không gì scale nó lên lại. Biến `worker.min` bị chặn ở mức tối thiểu 1.
- **Scale-in không giết job.** Worker có job đang chạy xin ECS bảo vệ (`app/jobs/ecs_protection.py`). Khi task bị dừng vì deploy, worker cho job 100 s để xong, rồi trả job về hàng đợi mà không tính là một lần thử. Trước đây job đó phải chờ sweeper 5 phút (`STUCK_THRESHOLD_SECONDS`).
- **Địa chỉ client.** Uvicorn chỉ tin VPC (ALB) và dải IP của CloudFront, nên rate limit theo IP thấy đúng địa chỉ người dùng mà người dùng không giả được. Tin `*` thì client tự ghi được `X-Forwarded-For`.
- **CloudFront tới ALB là HTTP.** HTTPS ở chặng này cần tên miền và chứng chỉ riêng. Chặng người dùng tới CloudFront vẫn là HTTPS.
- **Ngân sách kết nối DB.** `check "connection_budget"` báo khi `api.max` và `worker.max` cộng lại có thể mở nhiều kết nối hơn RDS nhận. Mặc định: 349 trên 407.
- **Embedder 4 vCPU, 8 GB, vì CPU.** Ngày 2026-11-17 đã thử 2 vCPU, 4 GB trên AWS: nạp một PDF giải tích 1,2 MB, embedder ở 98% CPU hơn 8 phút, health check hết giờ, và ECS thay task hai lần (14:14 và 14:18 UTC), mỗi lần giết lượt nhúng đang chạy. Autoscaling thêm task thứ hai, cũng ở 98%. Bộ nhớ chỉ đạt đỉnh 57% của 4 GB, nên giới hạn là CPU, không phải bộ nhớ: đừng giảm cỡ này nếu chưa đo lại lúc nạp tài liệu.
- **Model embedder nằm trong image** (`devops/embedder`), như reranker. Task mới không phải tải 2,4 GB từ Hugging Face trước khi phục vụ, và không phụ thuộc Hugging Face lúc chạy. Đổi model thì phải nhúng lại toàn bộ chunk, xem `devops/embedder/prepare_model.py`.
- **Moodle không nằm trong stack này.** Điền `moodle_base_url` nếu có.
- **Không tự đăng ký tài khoản.** Hạn mức sinh module tính theo tài khoản, nên để ai cũng đăng ký được thì một người tạo nhiều tài khoản để tiêu tiền nhà cung cấp. Biến `self_registration` mặc định tắt: admin tạo tài khoản, còn người mở từ Moodle có tài khoản qua LTI. Bản chạy trên máy (compose) vẫn mở.
- **Valkey không có mật khẩu,** chỉ có TLS và security group. Nó chỉ giữ bộ đếm rate limit.
