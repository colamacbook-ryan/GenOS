package install

// Registry trả về 8 Step theo đúng thứ tự StepID trong bảng trọng số của
// docs/handoff/05-installer.md. Tất cả 8 bước đều là cài đặt thật kể từ
// phiên này (Bước 8 — Hoàn tất — được cắm ở đây bằng finalizeStep, xem
// steps_finalize.go).
func Registry() []Step {
	return []Step{
		machineCheckStep{},
		runtimeStep{},
		pullStep{},
		secretsStep{},
		dataStep{},
		migrateStep{},
		servicesStep{},
		finalizeStep{},
	}
}
