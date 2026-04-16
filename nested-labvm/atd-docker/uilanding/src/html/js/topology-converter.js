/**
 * Topology Converter UI - JavaScript
 * Handles UI interactions and API calls for topology conversion
 */

(function() {
    'use strict';

    const API = {
        getCurrentTopology: '/td-api/topology-converter/current',
        getAvailableTopologies: '/td-api/topology-converter/available',
        getTopologyInfo: '/td-api/topology-converter/info',
        startConversion: '/td-api/topology-converter/convert',
        getStatus: '/td-api/topology-converter/status',
        getDeviceStatus: '/td-api/device-status'
    };

    let currentTopology = null;
    let availableTopologies = [];
    let conversionInProgress = false;
    let statusCheckInterval = null;
    let cvpMonitorInterval = null;
    let connectionLostCount = 0;
    let serverRestartDetected = false;
    let conversionCompleted = false;
    const MAX_CONNECTION_RETRIES = 60; // 5 minutes of retrying (5 sec intervals)
    const CVP_MONITOR_INTERVAL = 10000; // 10 seconds

    // Initialize on page load
    $(document).ready(function() {
        console.log('[TopologyConverter] Page loaded');
        init();
    });

    function init() {
        loadCurrentTopology();
        loadAvailableTopologies();
        setupEventListeners();
    }

    function setupEventListeners() {
        $('#target-topology-select').on('change', handleTopologySelection);
        $('#convert-btn').on('click', handleConvert);
        $('#cancel-btn').on('click', handleCancel);
        $('#refresh-status-btn').on('click', refreshStatus);
        $('#reload-page-btn').on('click', function() {
            window.location.href = '/';
        });
    }

    function loadCurrentTopology() {
        console.log('[TopologyConverter] Loading current topology');
        $.ajax({
            url: API.getCurrentTopology,
            method: 'GET',
            dataType: 'json',
            success: function(data) {
                console.log('[TopologyConverter] Current topology:', data);
                currentTopology = data;
                updateCurrentTopologyUI(data);
            },
            error: function(xhr, status, error) {
                console.error('[TopologyConverter] Failed to load current topology:', error);
                showError('Failed to load current topology: ' + error);
            }
        });
    }

    function loadAvailableTopologies() {
        console.log('[TopologyConverter] Loading available topologies');
        $.ajax({
            url: API.getAvailableTopologies,
            method: 'GET',
            dataType: 'json',
            success: function(data) {
                console.log('[TopologyConverter] Available topologies:', data);
                availableTopologies = data.topologies || [];
                updateTopologySelect();
            },
            error: function(xhr, status, error) {
                console.error('[TopologyConverter] Failed to load topologies:', error);
                showError('Failed to load available topologies: ' + error);
            }
        });
    }

    function updateCurrentTopologyUI(data) {
        $('#current-topology-name').text(data.name || 'Unknown');
        $('#current-topology-devices').text(data.node_count || '0');
        $('#current-topology-type').text(data.eos_type || 'Unknown');
        $('#current-topology-configlets').text(data.configlet_count || '0');
    }

    function updateTopologySelect() {
        const $select = $('#target-topology-select');
        $select.empty();
        $select.append('<option value="">-- Select a topology --</option>');

        availableTopologies.forEach(function(topo) {
            // Don't show current topology
            if (currentTopology && topo !== currentTopology.name) {
                $select.append(`<option value="${topo}">${topo}</option>`);
            }
        });
    }

    function handleTopologySelection() {
        const selected = $('#target-topology-select').val();
        console.log('[TopologyConverter] Selected topology:', selected);

        if (!selected) {
            $('#target-topology-info').hide();
            $('#convert-btn').prop('disabled', true);
            return;
        }

        // Load topology info
        $.ajax({
            url: API.getTopologyInfo + '?topology=' + encodeURIComponent(selected),
            method: 'GET',
            dataType: 'json',
            success: function(data) {
                console.log('[TopologyConverter] Topology info:', data);
                updateTargetTopologyUI(data);
                $('#target-topology-info').fadeIn();
                $('#convert-btn').prop('disabled', false);
            },
            error: function(xhr, status, error) {
                console.error('[TopologyConverter] Failed to load topology info:', error);
                showError('Failed to load topology information: ' + error);
            }
        });
    }

    function updateTargetTopologyUI(data) {
        $('#target-topology-name').text(data.name || 'Unknown');
        $('#target-topology-devices').text(data.node_count || '0');
        $('#target-topology-device-list').text((data.nodes || []).slice(0, 8).join(', ') +
            (data.nodes && data.nodes.length > 8 ? '...' : ''));
        $('#target-topology-configlets').text(data.configlet_count || '0');
    }

    function handleConvert() {
        const selected = $('#target-topology-select').val();

        if (!selected) {
            showError('Please select a target topology');
            return;
        }

        // Show confirmation
        const confirmed = confirm(
            `Are you sure you want to convert to ${selected}?\n\n` +
            `This will:\n` +
            `- Destroy all existing VMs\n` +
            `- Delete all OVS networks\n` +
            `- Create new topology\n` +
            `- Reconfigure CVP\n\n` +
            `This process takes 10-15 minutes and cannot be interrupted.\n\n` +
            `Type 'yes' to confirm.`
        );

        if (!confirmed) {
            return;
        }

        // Additional confirmation
        const userInput = prompt('Please type "yes" to confirm the conversion:');
        if (userInput !== 'yes') {
            alert('Conversion cancelled.');
            return;
        }

        startConversion(selected);
    }

    function startConversion(targetTopology) {
        console.log('[TopologyConverter] Starting conversion to:', targetTopology);

        // Disable UI
        $('#convert-btn').prop('disabled', true);
        $('#target-topology-select').prop('disabled', true);

        // Show progress section
        $('#conversion-progress').fadeIn();
        updatePhases('starting');

        // Start conversion
        $.ajax({
            url: API.startConversion,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                target_topology: targetTopology
            }),
            dataType: 'json',
            success: function(data) {
                console.log('[TopologyConverter] Conversion started:', data);
                conversionInProgress = true;

                if (data.status === 'started') {
                    updateStatus('Conversion started successfully');
                    appendLog(data.message || 'Conversion process initiated');

                    // Start status polling
                    startStatusPolling();
                } else {
                    showError('Failed to start conversion: ' + (data.error || 'Unknown error'));
                }
            },
            error: function(xhr, status, error) {
                console.error('[TopologyConverter] Failed to start conversion:', error);
                let errorMsg = 'Failed to start conversion: ' + error;

                try {
                    const response = JSON.parse(xhr.responseText);
                    errorMsg = response.error || errorMsg;
                } catch (e) {
                    // Ignore parse error
                }

                showError(errorMsg);
                $('#conversion-progress').hide();
                $('#convert-btn').prop('disabled', false);
                $('#target-topology-select').prop('disabled', false);
            }
        });
    }

    function startStatusPolling() {
        // Poll every 5 seconds
        statusCheckInterval = setInterval(function() {
            refreshStatus();
        }, 5000);
    }

    function stopStatusPolling() {
        if (statusCheckInterval) {
            clearInterval(statusCheckInterval);
            statusCheckInterval = null;
        }
    }

    function refreshStatus() {
        $.ajax({
            url: API.getStatus,
            method: 'GET',
            dataType: 'json',
            timeout: 10000,
            success: function(data) {
                console.log('[TopologyConverter] Status:', data);
                // Reset connection lost counter on success
                if (connectionLostCount > 0) {
                    connectionLostCount = 0;
                    // If we were in server restart mode and got a response, check if conversion completed
                    if (serverRestartDetected) {
                        appendLog('Server connection restored!');
                        serverRestartDetected = false;
                        // Check the new topology
                        checkConversionResult();
                        return;
                    }
                }
                handleStatusUpdate(data);
            },
            error: function(xhr, status, error) {
                console.error('[TopologyConverter] Failed to get status:', error);
                connectionLostCount++;

                if (conversionInProgress && connectionLostCount >= 2) {
                    // Server is likely restarting due to atdStartup
                    if (!serverRestartDetected) {
                        serverRestartDetected = true;
                        showServerRestartMessage();
                    }

                    if (connectionLostCount >= MAX_CONNECTION_RETRIES) {
                        // Too many retries, show manual check message
                        stopStatusPolling();
                        showManualCheckMessage();
                    } else {
                        // Update the waiting message with retry count
                        updateRetryCount(connectionLostCount, MAX_CONNECTION_RETRIES);
                    }
                }
            }
        });
    }

    function showServerRestartMessage() {
        updateStatus('Server is restarting... This is expected during topology conversion.');
        appendLog('Connection to server lost - this is normal during conversion.');
        appendLog('The server containers are being rebuilt. Please wait...');
        updatePhases('build');

        // Update the UI to show waiting state
        $('#status-callout').removeClass('topo-callout alert').addClass('topo-callout warning');
        $('#current-status').html(
            '<i class="fas fa-sync fa-spin"></i> Server restarting... ' +
            '<span id="retry-count"></span>'
        );
    }

    function updateRetryCount(current, max) {
        const remaining = Math.ceil((max - current) * 5 / 60);
        $('#retry-count').text(`(Waiting, ~${remaining} min remaining)`);
    }

    function showManualCheckMessage() {
        $('#current-status').html(
            '<i class="fas fa-exclamation-triangle"></i> Connection timeout. ' +
            'The conversion may have completed.'
        );
        appendLog('Connection timeout after multiple retries.');
        appendLog('Please refresh the page to check the current topology status.');

        // Show a button to reload
        $('#status-callout').after(
            '<div class="topo-callout info" style="margin-top: 1rem;">' +
            '<p>The server may still be starting up. Please wait a moment and then:</p>' +
            '<button class="topo-btn primary" onclick="location.reload();">' +
            '<i class="fas fa-refresh"></i> Refresh Page</button>' +
            '</div>'
        );
    }

    function checkConversionResult() {
        // After server restart, check what the current topology is
        $.ajax({
            url: API.getCurrentTopology,
            method: 'GET',
            dataType: 'json',
            success: function(data) {
                console.log('[TopologyConverter] Post-restart topology:', data);
                stopStatusPolling();
                conversionInProgress = false;
                conversionCompleted = true;

                // Keep progress visible and update header
                $('#conversion-progress .topo-card-header h5').html(
                    '<i class="fas fa-check-circle" style="color: #78d82c;"></i> Conversion Complete - Waiting for Devices'
                );

                // Show completion message (keep logs visible)
                $('#completion-message').fadeIn();
                $('#completion-message .topo-callout').html(
                    '<h5><i class="fas fa-check-circle"></i> Topology Conversion Completed!</h5>' +
                    '<p>The topology has been converted to: <strong>' + data.name + '</strong></p>' +
                    '<p>CVP is now configuring the devices. This may take several minutes.</p>'
                );

                appendLog('='.repeat(60));
                appendLog('Server is back online!');
                appendLog('Topology converted to: ' + data.name);
                appendLog('Starting device monitoring...');
                appendLog('='.repeat(60));

                updateStatus('Conversion completed! Now waiting for devices to come online...');
                $('#status-callout').removeClass('topo-callout alert warning').addClass('topo-callout success');
                updatePhases('devices');

                // Start monitoring device status
                startDeviceMonitoring();
            },
            error: function() {
                // Still can't connect, keep waiting
                appendLog('Still waiting for server to come back online...');
            }
        });
    }

    function handleStatusUpdate(data) {
        updateStatus(data.status || 'Unknown');

        if (data.phase) {
            updatePhases(data.phase);
        }

        if (data.log) {
            appendLog(data.log);
        }

        if (data.completed) {
            handleCompletion(data.success);
        }
    }

    function updateStatus(status) {
        $('#current-status').text(status);
    }

    function appendLog(message) {
        const $log = $('#log-output');
        const currentLog = $log.text();
        const timestamp = new Date().toLocaleTimeString();
        $log.text(currentLog + `\n[${timestamp}] ${message}`);

        // Scroll to bottom
        const logContainer = document.getElementById('conversion-log');
        logContainer.scrollTop = logContainer.scrollHeight;
    }

    function updatePhases(currentPhase) {
        const phases = [
            { id: 'validate', name: 'Validation', icon: 'check-circle' },
            { id: 'backup', name: 'Backup', icon: 'save' },
            { id: 'destroy', name: 'Destroy Old', icon: 'trash' },
            { id: 'update', name: 'Update Config', icon: 'edit' },
            { id: 'build', name: 'Build New', icon: 'cogs' },
            { id: 'cvp', name: 'Configure CVP', icon: 'network-wired' },
            { id: 'devices', name: 'Devices Ready', icon: 'server' }
        ];

        const phaseOrder = ['validate', 'backup', 'destroy', 'update', 'build', 'cvp', 'devices', 'completed'];
        const currentIndex = phaseOrder.indexOf(currentPhase);

        const $phases = $('#progress-phases');
        $phases.empty();

        phases.forEach(function(phase, index) {
            const isComplete = index < currentIndex || currentPhase === 'completed';
            const isCurrent = index === currentIndex;
            const statusClass = isComplete ? 'complete' : (isCurrent ? 'active' : 'pending');

            const phaseHtml = `
                <div class="phase-item ${statusClass}">
                    <div class="phase-icon">
                        <i class="fas fa-${phase.icon}"></i>
                    </div>
                    <div class="phase-name">${phase.name}</div>
                </div>
            `;
            $phases.append(phaseHtml);
        });
    }

    function handleCompletion(success) {
        stopStatusPolling();
        conversionInProgress = false;
        conversionCompleted = true;

        if (success) {
            // Keep progress section visible but update header
            $('#conversion-progress .topo-card-header h5').html(
                '<i class="fas fa-check-circle" style="color: #78d82c;"></i> Conversion Complete - Waiting for Devices'
            );
            updateStatus('Conversion completed! Now waiting for devices to come online...');
            $('#status-callout').removeClass('topo-callout alert warning').addClass('topo-callout success');

            // Show completion message above logs (don't hide progress)
            $('#completion-message').fadeIn();
            $('#completion-message .topo-callout').html(
                '<h5><i class="fas fa-check-circle"></i> Topology Conversion Completed!</h5>' +
                '<p>The topology infrastructure has been rebuilt. CVP is now configuring the devices.</p>' +
                '<p><strong>Note:</strong> Devices may take several minutes to boot and receive their configuration from CVP.</p>'
            );

            appendLog('='.repeat(60));
            appendLog('CONVERSION COMPLETE - Starting device monitoring...');
            appendLog('Devices will appear as "online" once they boot and eAPI is enabled.');
            appendLog('='.repeat(60));

            // Update phase to show we're in "devices" phase
            updatePhases('devices');

            // Start monitoring device status
            startDeviceMonitoring();
        } else {
            updateStatus('Conversion failed. Check logs for details.');
            $('#status-callout').removeClass('topo-callout success warning').addClass('topo-callout alert');
            appendLog('='.repeat(60));
            appendLog('CONVERSION FAILED - Check logs above for details');
            appendLog('='.repeat(60));
        }
    }

    function startDeviceMonitoring() {
        appendLog('Starting device status monitoring (checking every 10 seconds)...');

        // Show device status section
        if ($('#device-status-section').length === 0) {
            const deviceStatusHtml = `
                <div id="device-status-section" style="margin-top: 1.5rem;">
                    <h6><i class="fas fa-server"></i> Device Status:</h6>
                    <div id="device-status-grid" class="topo-callout info">
                        <p><i class="fas fa-spinner fa-spin"></i> Checking device status...</p>
                    </div>
                </div>
            `;
            $('#conversion-log').after(deviceStatusHtml);
        }

        // Initial check
        checkDeviceStatus();

        // Start polling
        cvpMonitorInterval = setInterval(function() {
            checkDeviceStatus();
        }, CVP_MONITOR_INTERVAL);
    }

    function stopDeviceMonitoring() {
        if (cvpMonitorInterval) {
            clearInterval(cvpMonitorInterval);
            cvpMonitorInterval = null;
        }
    }

    function checkDeviceStatus() {
        $.ajax({
            url: API.getDeviceStatus,
            method: 'GET',
            dataType: 'json',
            timeout: 15000,
            success: function(data) {
                console.log('[TopologyConverter] Device status:', data);
                updateDeviceStatusUI(data.devices || {});
            },
            error: function(xhr, status, error) {
                console.error('[TopologyConverter] Failed to get device status:', error);
                $('#device-status-grid').html(
                    '<p><i class="fas fa-exclamation-triangle" style="color: #fbb500;"></i> ' +
                    'Unable to check device status. Devices may still be booting...</p>'
                );
            }
        });
    }

    function updateDeviceStatusUI(devices) {
        const deviceNames = Object.keys(devices).sort();

        if (deviceNames.length === 0) {
            $('#device-status-grid').html(
                '<p><i class="fas fa-spinner fa-spin"></i> No devices found yet. Waiting for VMs to register...</p>'
            );
            return;
        }

        let onlineCount = 0;
        let offlineCount = 0;
        let totalCount = deviceNames.length;

        let gridHtml = '<div class="grid-x grid-padding-x">';

        deviceNames.forEach(function(name) {
            const device = devices[name];
            const isOnline = device.status === 'up';
            const statusIcon = isOnline ?
                '<i class="fas fa-check-circle" style="color: #78d82c;"></i>' :
                '<i class="fas fa-times-circle" style="color: #e30909;"></i>';
            const statusText = isOnline ? 'Online' : 'Offline';

            if (isOnline) {
                onlineCount++;
            } else {
                offlineCount++;
            }

            gridHtml += `
                <div class="cell small-6 medium-4 large-3" style="padding: 0.5rem;">
                    <div style="padding: 0.5rem; background: ${isOnline ? 'rgba(120,216,44,0.1)' : 'rgba(227,9,9,0.1)'}; border: 1px solid ${isOnline ? 'rgba(120,216,44,0.3)' : 'rgba(227,9,9,0.3)'}; border-radius: 4px; text-align: center; color: #fff;">
                        ${statusIcon} <strong>${name}</strong><br>
                        <small style="color: rgba(255,255,255,0.6);">${statusText}</small>
                    </div>
                </div>
            `;
        });

        gridHtml += '</div>';

        // Add summary
        const summaryHtml = `
            <div style="margin-bottom: 1rem; padding: 0.75rem; background: rgba(255,255,255,0.05); border: 1px solid rgba(251,181,0,0.15); border-radius: 4px; color: #fff;">
                <strong>Summary:</strong>
                <span style="color: #78d82c;">${onlineCount} online</span> /
                <span style="color: #e30909;">${offlineCount} offline</span> /
                ${totalCount} total
                ${offlineCount > 0 ?
                    ' <span style="color: rgba(255,255,255,0.5);">(devices are still booting, please wait...)</span>' :
                    ' <span style="color: #78d82c;">All devices ready!</span>'
                }
            </div>
        `;

        $('#device-status-grid').html(summaryHtml + gridHtml);

        // If all devices are online, update the phase
        if (onlineCount === totalCount && totalCount > 0) {
            updatePhases('completed');
            updateStatus('All devices are online and ready!');
            appendLog('All ' + totalCount + ' devices are now online!');
            stopDeviceMonitoring();

            // Update completion message
            $('#completion-message .topo-callout').html(
                '<h5><i class="fas fa-check-circle"></i> Lab Ready!</h5>' +
                '<p>All ' + totalCount + ' devices are online and configured.</p>' +
                '<button class="topo-btn primary" onclick="window.location.href=\'/\';">' +
                '<i class="fas fa-home"></i> Return to Home</button>'
            );

            // Update header
            $('#conversion-progress .topo-card-header h5').html(
                '<i class="fas fa-check-circle" style="color: #78d82c;"></i> Lab Ready!'
            );
        }
    }

    function handleCancel() {
        if (conversionInProgress) {
            alert('Cannot cancel while conversion is in progress.');
            return;
        }

        window.location.href = '/';
    }

    function showError(message) {
        alert('Error: ' + message);
        console.error('[TopologyConverter]', message);
    }

})();
