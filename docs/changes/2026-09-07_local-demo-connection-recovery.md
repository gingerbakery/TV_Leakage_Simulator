# 2026-09-07 로컬 렌더링 시연 연결 복구

4 lm Case를 선택했을 때 localhost:8788의 서버 리스너가 없었습니다. 기존 실행 세션89500도 이미 사라져 있었습니다. 따라서 당시 오류는 Python API에 연결할 수 없는 상태였습니다. 이전 stderr가 파일로 보존되지 않아 종료의 직접 원인(호스트의 프로세스 정리·외부 종료·프로세스 오류 등)은 확정하지 않았습니다.

## 복원

기존 소스 CPU 실행기를 동일한127.0.0.1:8788에 독립 백그라운드로 실행했습니다. 명령은 `_tools/python313/python.exe -u run_web.py --host 127.0.0.1 --port 8788 --strict-port`입니다. 시작PID는47768입니다. GPU 설정·설치·서버코드·프런트엔드코드는 변경하지 않았습니다.

Node child_process.spawn의 detached:true,windowsHide:true,stdin:ignore와파일stdout/stderr를 사용하고 unref()로명령의출력파이프에묶이지않게했습니다. launch명령완료뒤후속도구호출에서도리스너와health가유지됐습니다. 살아있는서버를재시작하거나부모커널을종료하지않았으므로부모종료후지속성까지실험한것은아닙니다.

## 확인

- /health:200,ok api_version=1.0.0
- off/1x/4x/16x의실제로업로드했던STEP4개모두디스크에남아있으며Binary scene API가200/BITSAMSC를반환했습니다.
- JSON scene의mesh_signature는4개모두 `mesh-fnv-pair-v1:328e162f9ba09e03`이며저장된검증bound-result의source_context와일치합니다. 현재브라우저메모리의결과는도구차단때문에직접읽지못했습니다.
- 서버재시작으로이전scene_token과job메모리는복원되지않습니다. 업로드STEP은디스크에보존되고새scene_token으로다시읽을수있습니다. 3D결과연결검사는형상서명을사용합니다.
- CUA의새Node runtime기동이OS error5(Access denied)로막혀브라우저에서다른Case로전환후복귀하는확인은직접실행하지못했습니다. 페이지를새로고침하거나브라우저Case메모리를변경하지않았습니다.

## 진단기록

현재PID·실행시각·로그경로: `outputs/local-demo-server/current.json`

API복구상태: `outputs/local-demo-server/recovery.json`

형상연결비교: `outputs/local-demo-server/scene-compatibility.json`

stdout: `C:/Users/Administrator/Documents/TV leakage simulator/outputs/local-demo-server/8788-2026-09-07T04-25-23-146Z.stdout.log`

stderr: `C:/Users/Administrator/Documents/TV leakage simulator/outputs/local-demo-server/8788-2026-09-07T04-25-23-146Z.stderr.log`
