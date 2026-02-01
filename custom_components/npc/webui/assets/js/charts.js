// Chart Management Module
class ChartManager {
    constructor() {
        this.monthlyChart = null;
        this.dailyChart = null;
        this.setupChartDefaults();
    }

    // Setup Chart.js default animations
    setupChartDefaults() {
        Chart.defaults.datasets.bar.animation = {
            duration: 1200,
            easing: 'easeInOutQuart',
            delay: (ctx) => ctx.dataIndex * 80
        };
        
        Chart.defaults.datasets.line.animation = {
            duration: 1200,
            easing: 'easeInOutQuart',
            delay: (ctx) => ctx.dataIndex * 30
        };
    }    // Tạo biểu đồ tháng (bao gồm kỳ hiện tại)
    createMonthlyChart(monthlyData, currentPeriod, onClickCallback) {
        if (this.monthlyChart) {
            this.monthlyChart.destroy();
        }

        // Chuẩn bị dữ liệu biểu đồ
        const labels = [];
        const consumptionData = [];
        const costData = [];
        const backgroundColors = [];
        const borderColors = [];

        const yearSet = new Set(monthlyData.SanLuong.map(item => item.Năm).filter(Boolean));
        const hasMultipleYears = yearSet.size > 1;

        // Thêm dữ liệu từ monthlyData
        monthlyData.SanLuong.forEach((item, index) => {
            const monthLabel = hasMultipleYears && item.Năm
                ? `Tháng ${item.Tháng}-${item.Năm}`
                : `Tháng ${item.Tháng}`;
            labels.push(monthLabel);
            consumptionData.push(parseInt(item["Điện tiêu thụ (KWh)"] || 0));
            
            // Tìm dữ liệu tiền điện tương ứng
            const correspondingCost = monthlyData.TienDien.find(cost =>
                cost.Tháng === item.Tháng && (!item.Năm || cost.Năm === item.Năm)
            );
            costData.push(parseInt(correspondingCost ? correspondingCost["Tiền Điện"] : 0));
            
            backgroundColors.push('rgba(147, 112, 219, 0.8)');
            borderColors.push('rgba(147, 112, 219, 1)');
        });        // Thêm dữ liệu kỳ hiện tại nếu có
        if (currentPeriod) {
            labels.push('Kỳ này');
            consumptionData.push(currentPeriod.consumption);
            costData.push(currentPeriod.cost);
            backgroundColors.push('rgba(255, 193, 7, 0.8)'); // Màu vàng cho kỳ hiện tại
            borderColors.push('rgba(255, 193, 7, 1)');
        }

        const ctx = document.getElementById('monthlyChart');
        this.monthlyChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Tiêu thụ (kWh)',
                        data: consumptionData,
                        backgroundColor: backgroundColors,
                        borderColor: borderColors,
                        borderWidth: 1,
                        yAxisID: 'y1',
                        datalabels: { display: false }
                    },
                    {
                        label: 'Hóa đơn (VND)',
                        data: costData,
                        backgroundColor: costData.map((_, index) => 
                            index === costData.length - 1 && currentPeriod ? 
                            'rgba(255, 152, 0, 0.8)' : 'rgba(233, 97, 171, 0.8)'
                        ),
                        borderColor: costData.map((_, index) => 
                            index === costData.length - 1 && currentPeriod ? 
                            'rgba(255, 152, 0, 1)' : 'rgba(233, 97, 171, 1)'
                        ),
                        borderWidth: 1,
                        yAxisID: 'y2',
                        datalabels: { display: false }
                    }
                ]
            },                options: {
                animation: {
                    duration: 800, // Giảm thời gian animation
                    easing: 'easeOutQuart'
                },
                interaction: {
                    intersect: false,
                    mode: 'index'
                },
                hover: {
                    animationDuration: 0 // Tắt animation khi hover
                },
                scales: {                    y1: {
                        type: 'linear',
                        position: 'left',
                        beginAtZero: true,
                        ticks: { 
                            color: this.getCurrentThemeColors().textColor
                        },
                        title: { 
                            display: true, 
                            text: 'Tiêu thụ (kWh)', 
                            color: this.getCurrentThemeColors().textColor
                        }
                    },                    y2: {
                        type: 'linear',
                        position: 'right',
                        beginAtZero: true,
                        ticks: { 
                            color: this.getCurrentThemeColors().textColor
                        },
                        title: { 
                            display: true, 
                            text: 'Hóa đơn (VND)', 
                            color: this.getCurrentThemeColors().textColor
                        },
                        grid: { drawOnChartArea: false }
                    }
                },                plugins: {                    legend: { 
                        labels: { 
                            color: this.getCurrentThemeColors().textColor
                        } 
                    },
                    tooltip: {
                        animation: {
                            duration: 0 // Tắt animation tooltip để tránh nháy
                        },                        callbacks: {
                            label: function(context) {
                                const isCurrentPeriod = context.label === 'Kỳ này';
                                
                                if (context.datasetIndex === 0) {
                                    // Dataset tiêu thụ
                                    let label = `${context.dataset.label}: ${context.parsed.y.toFixed(2)} kWh`;
                                    if (isCurrentPeriod && currentPeriod) {
                                        label += `\n📅 Kỳ: ${currentPeriod.period.start.toLocaleDateString('vi-VN')} → ${currentPeriod.period.end.toLocaleDateString('vi-VN')}`;
                                        label += `\n📊 Đã có ${currentPeriod.days} ngày dữ liệu`;
                                    }
                                    return label;
                                } else {
                                    // Dataset hóa đơn
                                    let label = `${context.dataset.label}: ${context.parsed.y.toLocaleString()} VND`;
                                    if (isCurrentPeriod) {
                                        label += ` (tạm tính)`;
                                        if (currentPeriod && currentPeriod.details) {
                                            label += `\n💡 Trước thuế: ${currentPeriod.details.subtotal.toLocaleString()} VND`;
                                            label += `\n🏛️ Thuế 8%: ${currentPeriod.details.tax.toLocaleString()} VND`;
                                        }
                                    }
                                    return label;
                                }
                            }
                        }
                    }
                },
                maintainAspectRatio: false,
                responsive: true,
                onClick: onClickCallback
            }
        });

        return this.monthlyChart;
    }

    // Tạo biểu đồ ngày
    createDailyChart(filteredData) {
        const data = filteredData.filter(day => day["Điện tiêu thụ (kWh)"] > 0);
        data.sort((a, b) => 
            new Date(a.Ngày.split('-').reverse().join('-')) - 
            new Date(b.Ngày.split('-').reverse().join('-'))
        );

        // Tính trend cho mỗi ngày
        data.forEach((day, idx, arr) => {
            if (idx === 0) {
                day._trend = 'flat';
                day._trendValue = 0;
            } else {
                const prev = arr[idx-1]["Điện tiêu thụ (kWh)"];
                const val = day["Điện tiêu thụ (kWh)"];
                day._trend = val > prev ? 'up' : (val < prev ? 'down' : 'flat');
                day._trendValue = val - prev;
            }
        });

        const dailyLabels = data.map(day => day.Ngày);
        const dailyDataValues = data.map(day => day["Điện tiêu thụ (kWh)"]);

        // Highlight max/min
        const maxVal = Math.max(...dailyDataValues);
        const minVal = Math.min(...dailyDataValues);
        
        const pointBackgroundColors = dailyDataValues.map(v => 
            v === maxVal ? '#2ecc40' : v === minVal ? '#e74c3c' : 'rgba(233,97,171,0.6)'
        );
        const pointRadius = dailyDataValues.map(v => 
            v === maxVal || v === minVal ? 7 : 4
        );
        const pointStyle = dailyDataValues.map(v => 
            v === maxVal ? 'star' : v === minVal ? 'triangle' : 'circle'
        );

        if (this.dailyChart) {
            this.dailyChart.destroy();
        }

        const ctx = document.getElementById('dailyChart');
        this.dailyChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: dailyLabels,
                datasets: [{
                    label: 'Tiêu thụ (kWh)',
                    data: dailyDataValues,
                    fill: true,
                    backgroundColor: 'rgba(233, 97, 171, 0.2)',
                    borderColor: 'rgba(233, 97, 171, 1)',
                    borderWidth: 2,
                    pointBackgroundColor: pointBackgroundColors,
                    pointRadius: pointRadius,
                    pointStyle: pointStyle,
                    pointHoverRadius: 8,
                    datalabels: { display: false }
                }]
            },
            options: {
                animation: {
                    duration: 800,
                    easing: 'easeOutQuart'
                },
                interaction: {
                    intersect: false,
                    mode: 'index'
                },
                hover: {
                    animationDuration: 0
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { 
                            color: this.getCurrentThemeColors().textColor
                        },
                        title: { 
                            display: true, 
                            text: 'Tiêu thụ (kWh)', 
                            color: this.getCurrentThemeColors().textColor
                        }
                    },
                    x: {
                        ticks: { 
                            color: this.getCurrentThemeColors().textColor
                        }
                    }
                },
                plugins: {
                    legend: { 
                        labels: { 
                            color: this.getCurrentThemeColors().textColor
                        } 
                    },
                    tooltip: {
                        animation: {
                            duration: 0
                        },
                        callbacks: {
                            label: function(context) {
                                const value = context.parsed.y;
                                return `${context.dataset.label}: ${value.toFixed(2)} kWh`;
                            }
                        }
                    }
                },
                maintainAspectRatio: false,
                responsive: true
            }
        });

        return this.dailyChart;
    }

    // Get current theme colors
    getCurrentThemeColors() {
        const currentTheme = document.body.getAttribute('data-theme') || 'dark-gradient';
        const themeConfig = this.getThemeChartConfig(currentTheme);
        return themeConfig;
    }

    // Theme-based chart configurations
    getThemeChartConfig(themeName) {
        const configs = {
            'dark-gradient': {
                textColor: '#cbd5e1'
            },
            'cyberpunk': {
                textColor: '#f0eaff'
            },
            'neon-dreams': {
                textColor: '#e9f0ff'
            },
            'aurora-borealis': {
                textColor: '#f1f5f9'
            },
            'synthwave': {
                textColor: '#fef08a'
            },
            'glassmorphism': {
                textColor: '#f8fafc'
            },
            'neubrutalism': {
                textColor: '#1f2937'
            },
            'matrix-rain': {
                textColor: '#86efac'
            },
            'sunset-vibes': {
                textColor: '#f8fafc'
            },
            'ocean-depth': {
                textColor: '#e2e8f0'
            },
            'midnight-purple': {
                textColor: '#e2e8f0'
            },
            'golden-hour': {
                textColor: '#1f2937'
            },
            'forest-mist': {
                textColor: '#f8fafc'
            },
            'cosmic-dust': {
                textColor: '#f8fafc'
            },
            'tokyo-night': {
                textColor: '#e2e8f0'
            },
            'minimal-light': {
                textColor: '#1f2937'
            }
        };

        return configs[themeName] || configs['dark-gradient'];
    }
}

// Export for global access
window.ChartManager = ChartManager;
