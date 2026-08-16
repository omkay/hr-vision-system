<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>بطاقة موظف - New Park</title>
    <link rel="icon" type="image/png" href="{{ asset('logo.png') }}">

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Almarai:wght@300;400;700;800&display=swap" rel="stylesheet">

    <style>
        :root {
            --np-dark-blue: #0054a6;
            --np-light-blue: #00bff3;
            --np-red: #ed1c24;
            --np-bg: #f5f7fa;
            --card-radius: 28px;
        }

        * {
            box-sizing: border-box;
            -webkit-font-smoothing: antialiased;
        }

        body {
            margin: 0;
            padding: 20px;
            font-family: 'Almarai', sans-serif;
            background-color: var(--np-bg);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow-x: hidden;
            position: relative;
        }

        /* 🔵 الدوائر العائمة الخلفية (نفس واجهة تسجيل دخول Flutter) */
        body::before, body::after {
            content: '';
            position: fixed;
            border-radius: 50%;
            z-index: -1;
            filter: blur(70px);
        }

        /* الدائرة العلوية اليسرى */
        body::before {
            width: 500px; /* زدنا الحجم من 350 */
            height: 500px;
            background: rgba(0, 84, 166, 0.4); /* زدنا التشبع من 0.08 */
            top: -150px;
            left: -150px;
            filter: blur(80px); /* زيادة النعومة */
        }

        body::after {
            width: 600px; /* زدنا الحجم من 450 */
            height: 600px;
            background: rgba(0, 191, 243, 0.4); /* زدنا التشبع من 0.12 */
            bottom: -200px;
            right: -200px;
            filter: blur(80px);
        }

        .card {
            width: 100%;
            max-width: 850px;
            background: rgba(255, 255, 255, 0.85);
            backdrop-filter: blur(15px);
            border-radius: var(--card-radius);
            overflow: hidden;
            box-shadow: 0 30px 60px rgba(0, 84, 166, 0.1);
            border: 1px solid rgba(0, 191, 243, 0.2);
            position: relative;
            z-index: 1;
            transition: transform 0.3s ease;
        }

        /* الشريط العلوي المميز لنيوبارك */
        .header-accent {
            position: absolute;
            top: 0;
            right: 50px;
            height: 6px;
            width: 50px;
            background: var(--np-dark-blue);
            border-radius: 0 0 10px 10px;
        }

        .header {
            padding: 45px 50px 25px 50px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .header-right {
            text-align: right;
        }

        .company-label {
            font-size: 13px;
            color: var(--np-light-blue);
            font-weight: 800;
            letter-spacing: 1.5px;
            margin-bottom: 8px;
            display: block;
            text-transform: uppercase;
        }

        .employee-name {
            font-size: 34px;
            font-weight: 800;
            color: var(--np-dark-blue);
            margin: 0;
            letter-spacing: -0.5px;
        }

        .logo-box {
            background: rgba(0, 84, 166, 0.04);
            width: 110px;
            height: 110px;
            border-radius: 22px;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid rgba(0, 84, 166, 0.05);
        }

        .logo-box img {
            width: 75%;
            height: auto;
            object-fit: contain;
        }

        .body-content {
            padding: 20px 50px 45px 50px;
        }

        /* شبكة المعلومات - عمودين */
        .info-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 25px 50px;
        }
        .info-item {
            display: flex;
            flex-direction: column;
            gap: 6px;
            border-bottom: 1px solid #f0f4f8;
            padding-bottom: 14px;
        }

        .label {
            color: var(--np-dark-blue);
            font-weight: 800;
            font-size: 13px;
            opacity: 0.85;
        }

        .value {
            font-weight: 400;
            color: #2d3748;
            font-size: 16px;
        }

        .footer {
            background: #ffffff;
            padding: 20px 50px;
            border-top: 1px solid #edf2f7;
            display: flex;
            flex-direction: column; /* ترتيب العناصر عمودياً */
            align-items: center;
            gap: 8px; /* المسافة بين السطرين */
        }

        /* تنسيق السطر الأول */
        .footer-brand-row {
            display: flex;
            gap: 5px;
            align-items: center;
        }

        /* تنسيق السطر الثاني */
        .footer-info-row {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 12px;
        }

        .brand-text-dark { color: var(--np-dark-blue); font-weight: 800; font-size: 16px; }
        .brand-text-light { color: var(--np-light-blue); font-weight: 800; font-size: 16px; }

        /* الفاصل العمودي */
        .brand-divider {
            width: 1px;
            height: 14px;
            background: #cbd5e1;
        }

        .brand-dept {
            color: #64748b;
            font-size: 13px;
            font-weight: 700;
        }

        .web-link {
            color: #94a3b8;
            font-size: 13px;
            text-decoration: none;
            font-weight: 400;
            transition: color 0.2s ease;
        }

        .web-link:hover {
            color: var(--np-light-blue);
        }

        /* التجاوب مع الجوال */
        @media (max-width: 768px) {
            body { padding: 10px; }
            .card { border-radius: 20px; }
            .header { flex-direction: column-reverse; text-align: center; padding: 35px 25px; gap: 25px; }
            .header-right { text-align: center; }
            .info-grid { grid-template-columns: 1fr; gap: 18px; }
            .body-content { padding: 20px 30px 40px 30px; }
            .employee-name { font-size: 26px; }
            .header-accent { right: calc(50% - 25px); }
        }
    </style>
</head>
<body>

<div class="card">
    <div class="header-accent"></div>

    <div class="header">
        <div class="header-right">
            <span class="company-label">New Park</span>
            <h1 class="employee-name">{{ $employee->name }}</h1>
        </div>

        <div class="logo-box">
            <img src="{{ asset('logo.png') }}" alt="New Park Logo">
        </div>
    </div>

    <div class="body-content">
        <div class="info-grid">
            <div class="info-item">
                <span class="label">الإدارة</span>
                <span class="value">{{ $employee->Administration }}</span>
            </div>

            <div class="info-item">
                <span class="label">القسم</span>
                <span class="value">{{ $employee->department ?: 'غير موجود' }}</span>
            </div>

            <div class="info-item">
                <span class="label">المنصب</span>
                <span class="value">{{ $employee->position }}</span>
            </div>

            <div class="info-item">
                <span class="label">المدير المباشر</span>
                <span class="value">{{ $employee->direct_maneger }}</span>
            </div>

            <div class="info-item">
                <span class="label">تاريخ المباشرة</span>
                <span class="value">{{ $employee->start_date }}</span>
            </div>

            <div class="info-item">
                <span class="label">موقع العمل</span>
                <span class="value">{{ $employee->work_site }}</span>
            </div>

            <div class="info-item">
                <span class="label">الوردية</span>
                <span class="value">{{ $employee->sheft }}</span>
            </div>

            <div class="info-item">
                <span class="label">رقم الهاتف</span>
                <span class="value">{{ $employee->phone_num ?: 'غير موجود' }}</span>
            </div>
        </div>
    </div>
    <div class="footer">
        <div class="footer-brand-row">
            <span class="brand-text-light">Park</span>
            <span class="brand-text-dark">New</span>
        </div>

        <div class="footer-info-row">
            <span class="brand-dept">إدارة تكنولوجيا المعلومات</span>
            <div class="brand-divider"></div>
            <a href="https://www.newpark.co" class="web-link" target="_blank">www.newpark.co © 2026</a>
        </div>
    </div>
</div>

</body>
</html>
