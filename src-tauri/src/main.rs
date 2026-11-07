// Assets Manager 데스크톱 셸 (Tauri v2)
// - 시작할 때 Python 사이드카(app/core/server.py)를 띄우고, 종료할 때 끈다.
// - UI는 app/ui를 그대로 쓰고, 네이티브 파일 드래그는 tauri-plugin-drag가 담당한다.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    path::PathBuf,
    process::{Child, Command},
    sync::Mutex,
};
use tauri::{Manager, RunEvent};

struct Sidecar(Mutex<Option<Child>>);

fn spawn_server() -> Option<Child> {
    // MVP: 개발 폴더 기준으로 server.py를 찾는다. 배포 단계(3단계)에서 번들 리소스로 바꾼다.
    let app_dir = std::env::var("ASSETS_APP_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."));
    let script = app_dir.join("core").join("server.py");
    // exe를 직접 더블클릭해도 동작하도록 여러 이름을 차례로 시도한다.
    let candidates: Vec<String> = match std::env::var("ASSETS_PYTHON") {
        Ok(p) => vec![p],
        Err(_) if cfg!(windows) => vec!["py".into(), "python".into()],
        Err(_) => vec!["python3".into(), "python".into()],
    };
    for python in candidates {
        let mut cmd = Command::new(&python);
        cmd.arg(&script);
        if let Ok(root) = std::env::var("ASSETS_ROOT") {
            cmd.arg(root);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: 콘솔 창 숨김
        }
        match cmd.spawn() {
            Ok(child) => return Some(child),
            Err(e) => eprintln!("{python} 실행 실패 ({}): {e}", script.display()),
        }
    }
    None
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_drag::init())
        .setup(|app| {
            app.manage(Sidecar(Mutex::new(spawn_server())));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tauri 앱을 만들지 못했습니다");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Sidecar>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
