﻿import time
import glob
import numpy as np
import cv2
import time
import seaborn as sns
import sklearn.cluster as cluster
import random as rng
import collections
import os
import sys

from PyQt5 import QtCore, QtGui, QtWidgets
from playback_manager import PlaybackManager, Event, TestFigure
from log_object import LogObject
from background_subtractor import BackgroundSubtractor, MOGParameters

def nothing(x):
    pass

def round_up_to_odd(f):
    return np.ceil(f) // 2 * 2 + 1

PARAMETER_TYPES = {
            "detection_size": int,
	        "mog_var_thresh": int,
	        "min_fg_pixels": int,
	        "median_size": int,
	        "nof_bg_frames": int,
	        "learning_rate": float,
	        "dbscan_eps": int,
	        "dbscan_min_samples": int
        }

class Detector():

	def __init__(self, image_provider):
		self.image_provider = image_provider
		self.bg_subtractor = BackgroundSubtractor(image_provider)
		self.resetParameters()
		self.detections = [None]
		self.vertical_detections = []

		self.current_ind = 0
		self.current_len = 0

		self.ts = 0

		# When detector parameters change.
		self.state_changed_event = Event()
		self.bg_subtractor.state_changed_signal.connect(self.state_changed_event)

		# When results change (e.g. when a new frame is displayed).
		self.data_changed_event = Event()

		# When detector has computed all available frames.
		self.all_computed_event = Event()

		self._show_detections = False
		self._show_echogram_detections = False
		self._show_detection_size = False
		self.show_bgsub = False

		self.applied_parameters = None

		# [flag] Whether detector is computing all the frames.
		self.computing = False

		# [trigger] Terminate computing process.
		self.stop_computing = False

		# [flag] Calculate detections on event. Otherwise uses precalculated results.
		self.compute_on_event = False

	def resetParameters(self):
		self.bg_subtractor.resetParameters()
		self.parameters = DetectorParameters(mog_parameters=self.bg_subtractor.mog_parameters)

	def initMOG(self, clear_detections=True):
		if clear_detections:
			self.clearDetections()
		self.bg_subtractor.initMOG()

	def compute_from_event(self, tuple):
		if tuple is None:
			return

		ind, img = tuple
		if self.compute_on_event:
			self.compute(ind, img)
		else:
			self.data_changed(ind)

	def compute(self, ind, image, get_images=False):
		images = self.computeBase(ind, image, get_images)
		self.data_changed(ind)
		if get_images:
			return images

	def data_changed(self, ind):
		dets = self.detections[ind]
		self.current_ind = ind
		self.current_len = 0 if dets is None else len(self.detections[ind])
		self.data_changed_event(self.current_len)

	def getPolarTransform(self):
		if hasattr(self.image_provider, "playback_thread"):
			return self.image_provider.playback_thread.polar_transform
		else:
			return None

	def computeBase(self, ind, image, get_images=False):
		# Update timestamp, TODO read from data
		self.ts += 0.1

		image_o = image_o_gray = image
		fg_mask_mog = self.bg_subtractor.subtractBG(image_o)
		if fg_mask_mog is None:
			return

		fg_mask_cpy = fg_mask_mog
		fg_mask_filt = cv2.medianBlur(fg_mask_cpy, self.parameters.median_size)
		image_o_rgb = cv2.applyColorMap(image_o, cv2.COLORMAP_OCEAN)

		data_tr = np.nonzero(np.asarray(fg_mask_filt))
		data = np.asarray(data_tr).T
		detections = []

		if data.shape[0] >= self.parameters.min_fg_pixels:

			# DBSCAN clusterer, NOTE: parameters should be in UI / read from file
			clusterer = cluster.DBSCAN(eps=self.parameters.dbscan_eps, min_samples=self.parameters.dbscan_min_samples)		
			labels = clusterer.fit_predict(data)
		
			data = data[labels != -1]
			labels = labels[labels != -1]

			if labels.shape[0] > 0:

				polar_transform = self.getPolarTransform()

				for label in np.unique(labels):
					foo = data[labels == label]
					if foo.shape[0] < 2:
						continue
			
					d = Detection(label)
					d.init_from_data(foo, self.parameters.detection_size, polar_transform)
					detections.append(d)

				if get_images:
					colors = sns.color_palette('deep', np.unique(labels).max() + 1)
					for d in detections:
						image_o_rgb = d.visualize(image_o_rgb, colors, self._show_detection_size)

		self.detections[ind] = detections
		#self.detections_clearable = True

		if get_images:
			return (fg_mask_mog, image_o_gray, image_o_rgb, fg_mask_filt)

	def computeAll(self):
		self.computing = True
		self.stop_computing = False
		self.compute_on_event = False
		self.state_changed_event()

		if self.bg_subtractor.parametersDirty():
			self.initMOG()
			if self.bg_subtractor.parametersDirty():
				LogObject().print("Stopped before detecting.")
				self.abortComputing(True)
				return

		count = self.image_provider.getFrameCount()
		ten_perc = 0.1 * count
		print_limit = 0
		for ind in range(count):
			if ind > print_limit:
				LogObject().print("Detecting:", int(float(ind) / count * 100), "%")
				print_limit += ten_perc

			if self.stop_computing:
				LogObject().print("Stopped detecting at", ind)
				self.abortComputing(False)
				return

			img = self.image_provider.getFrame(ind)
			self.computeBase(ind, img)

		LogObject().print("Detecting: 100 %")
		self.computing = False
		#self.detections_clearable = True
		self.applied_parameters = self.parameters.copy()

		self.updateVerticalDetections()

		self.state_changed_event()
		self.all_computed_event()

	def updateVerticalDetections(self):
		LogObject().print("Updated")
		self.vertical_detections = [[d.distance for d in dets if d.center is not None] if dets is not None else [] for dets in self.detections]

	def abortComputing(self, mog_aborted):
		self.stop_computing = False
		self.computing = False
		self.compute_on_event = True

		self.state_changed_event()
		self.applied_parameters = None

		if mog_aborted:
			self.bg_subtractor.abortComputing()

	def clearDetections(self):
		LogObject().print("Cleared")
		nof_frames = self.image_provider.getFrameCount()
		self.detections = [None] * nof_frames
		self.vertical_detections = []
		#self.detections_clearable = False
		self.applied_parameters = None
		self.compute_on_event = True
		self.state_changed_event()
		
	def overlayDetections(self, image, detections=None):
		if detections is None:
			detections = self.getCurrentDetection()

		colors = sns.color_palette('deep', max([0] + [d.label + 1 for d in detections]))
		for d in detections:
			image = d.visualize(image, colors, self._show_detection_size)

		return image

	def setDetectionSize(self, value):
		try:
			self.parameters.detection_size = int(value)
			self.state_changed_event()
		except ValueError as e:
			LogObject().print(e)

	def setMOGVarThresh(self, value):
		try:
			self.bg_subtractor.mog_parameters.mog_var_thresh = int(value)
			self.state_changed_event()
		except ValueError as e:
			LogObject().print(e)

	def setMinFGPixels(self, value):
		try:
			self.parameters.min_fg_pixels = int(value)
			self.state_changed_event()
		except ValueError as e:
			LogObject().print(e)

	def setMedianSize(self, value):
		try:
			self.parameters.median_size = int(value)
			self.state_changed_event()
		except ValueError as e:
			LogObject().print(e)

	def setNofBGFrames(self, value):
		try:
			self.bg_subtractor.mog_parameters.nof_bg_frames = int(value)
			self.state_changed_event()
		except ValueError as e:
			LogObject().print(e)

	def setLearningRate(self, value):
		try:
			self.bg_subtractor.mog_parameters.learning_rate = float(value)
			self.state_changed_event()
		except ValueError as e:
			LogObject().print(e)

	def setDBScanEps(self, value):
		try:
			self.parameters.dbscan_eps = int(value)
			self.state_changed_event()
		except ValueError as e:
			LogObject().print(e)

	def setDBScanMinSamples(self, value):
		try:
			self.parameters.dbscan_min_samples = int(value)
			self.state_changed_event()
		except ValueError as e:
			LogObject().print(e)

	def setShowDetections(self, value):
		self._show_detections = value
		if not self._show_detections:
			self.data_changed_event(0)

	def setShowEchogramDetections(self, value):
		self._show_echogram_detections = value
		if not self._show_echogram_detections:
			self.data_changed_event(0)

	def setShowSize(self, value):
		self._show_detection_size = value

	def toggleShowBGSubtraction(self):
		self.show_bgsub = not self.show_bgsub

	def setShowBGSubtraction(self, value):
		self.show_bgsub = value

	def getDetection(self, ind):
		dets = self.detections[ind]
		if dets is None:
			return []

		return [d for d in dets if d.center is not None]

	def getDetections(self):
		return [[d for d in dets if d.center is not None] if dets is not None else [] for dets in self.detections]

	def getCurrentDetection(self):
		return self.getDetection(self.current_ind)

	def getParameterDict(self):
		if self.parameters is not None:
			return self.parameters.getParameterDict()
		else:
			return None

	def bgSubtraction(self, image):
		return self.bg_subtractor.subtractBGFiltered(image, self.parameters.median_size)

	def parametersDirty(self):
		return self.parameters != self.applied_parameters or self.bg_subtractor.parametersDirty()

	def allCalculationAvailable(self):
		return self.parametersDirty() and not self.bg_subtractor.initializing

	def saveDetectionsToFile(self, path):
		"""
		Writes current detections to a file at path. Values are separated by ';'.
		"""

		# Default formatting
		f1 = "{:.5f}"
		lineBase1 = "{};" + "{};{};{};".format(f1,f1,f1)

		try:
			with open(path, "w") as file:
				file.write("frame;length;distance;angle;corner1 x;corner1 y;corner2 x;corner2 y;corner3 x;corner3 y;corner4 x;corner4 y\n")
				for frame, dets in enumerate(self.detections):
					if dets is not None:
						for d in dets:
							if d.corners is not None:
								file.write(lineBase1.format(frame, d.length, d.distance, d.angle))
								file.write(d.cornersToString(";"))
								file.write("\n")
				LogObject().print("Detections saved to path:", path)

		except PermissionError as e:
			LogObject().print("Cannot open file {}. Permission denied.".format(path))

	def loadDetectionsFromFile(self, path):
		"""
		Loads a file from path. Values are expected to be separated by ';'.
		"""
		try:
			with open(path, 'r') as file:
				self.clearDetections()
				nof_frames = self.image_provider.getFrameCount()
				ignored_dets = 0

				header = file.readline()

				for line in file:
					split_line = line.split(';')
					frame = int(split_line[0])

					if frame >= nof_frames:
						ignored_dets += 1
						continue

					length = float(split_line[1])
					distance = float(split_line[2])
					angle = float(split_line[3])

					c1 = [float(split_line[5]), float(split_line[4])]
					c2 = [float(split_line[7]), float(split_line[6])]
					c3 = [float(split_line[9]), float(split_line[8])]
					c4 = [float(split_line[11]), float(split_line[10])]
					corners = np.array([c1, c2, c3, c4])

					det = Detection(0)
					det.init_from_file(corners, length, distance, angle)

					if self.detections[frame] is None:
						self.detections[frame] = [det]
					else:
						self.detections[frame].append(det)

				self.updateVerticalDetections()
				self.compute_on_event = False
				if ignored_dets > 0:
					LogObject().print("Encountered {} detections that were out of range {}.".format(ignored_dets, nof_frames))


		except PermissionError as e:
			LogObject().print("Cannot open file {}. Permission denied.".format(path))

	def getSaveDictionary(self):
		"""
		Returns a dictionary of detection data to be saved in SaveManager.
		"""
		detections = {}
		for frame, dets in enumerate(self.detections):
			if dets is None:
				continue

			dets_in_frame = [d.convertToWritable() for d in dets if d.corners is not None]
			if len(dets_in_frame) > 0:
				detections[str(frame)] = dets_in_frame

		return detections

	def applySaveDictionary(self, data):
		"""
		Load detections from data provided by SaveManager.
		"""
		#self.clearDetections()
		#TODO: get polar transform
		polar_transform = self.getPolarTransform()

		for str_frame, frame_dets_data in data.items():
			frame = int(str_frame)
			frame_dets = []
			for det_data in frame_dets_data:
				label = det_data[0]
				det_data = det_data[1]
				det = Detection(int(label))
				det.init_from_data(det_data, self.parameters.detection_size, polar_transform)
				frame_dets.append(det)

			self.detections[frame] = frame_dets

		self.applied_parameters = self.parameters.copy()
		self.bg_subtractor.setParameters(self.parameters.mog_parameters)

		self.updateVerticalDetections()
		#print("---", self.vertical_detections)
		self.compute_on_event = False
		self.state_changed_event()
		self.all_computed_event()


class DetectorParameters:
	def __init__(self, mog_parameters=None, detection_size=10, min_fg_pixels=25, median_size=3, dbscan_eps=10, dbscan_min_samples=10):

		if mog_parameters is None:
			self.mog_parameters = MOGParameters()
		else:
			self.mog_parameters = mog_parameters

		self.detection_size = detection_size
		self.min_fg_pixels = min_fg_pixels
		self.median_size = median_size
		self.dbscan_eps = dbscan_eps
		self.dbscan_min_samples = dbscan_min_samples

	def __eq__(self, other):
		if not isinstance(other, DetectorParameters):
			return False

		value = self.mog_parameters == other.mog_parameters \
			and self.detection_size == other.detection_size \
			and self.min_fg_pixels == other.min_fg_pixels \
			and self.median_size == other.median_size \
			and self.dbscan_eps == other.dbscan_eps \
			and self.dbscan_min_samples == other.dbscan_min_samples

		#LogObject().print(self, other, value)
		return value

	def __repr__(self):
		return "Parameters: {} {} {} {} {}".format(self.detection_size, self.min_fg_pixels, self.median_size, self.dbscan_eps, self.dbscan_min_samples)

	def copy(self):
		mog_parameters = self.mog_parameters.copy()
		return DetectorParameters( mog_parameters, self.detection_size, self.min_fg_pixels, self.median_size, self.dbscan_eps, self.dbscan_min_samples )

	def getParameterDict(self):
		return {
            "detection_size": self.detection_size,
	        "mog_var_thresh": self.mog_parameters.mog_var_thresh,
	        "min_fg_pixels": self.min_fg_pixels,
	        "median_size": self.median_size,
	        "nof_bg_frames": self.mog_parameters.nof_bg_frames,
	        "learning_rate": self.mog_parameters.learning_rate,
	        "dbscan_eps": self.dbscan_eps,
	        "dbscan_min_samples": self.dbscan_min_samples
        }

	def setParameterDict(self, dict):
		for key, value in dict.items():
			if hasattr(self, key) and key in PARAMETER_TYPES:
				try:
					setattr(self, key, PARAMETER_TYPES[key](value))
				except ValueError as e:
					LogObject().print("Error: Invalid value in detector parameters file,", e)
			elif hasattr(self.mog_parameters, key) and key in PARAMETER_TYPES:
				try:
					setattr(self.mog_parameters, key, PARAMETER_TYPES[key](value))
				except ValueError as e:
					LogObject().print("Error: Invalid value in detector parameters file,", e)
			else:
				LogObject().print("Error: Invalid parameters: {}: {}".format(key, value))




class Detection:
	def __init__(self, label):
		self.label = label
		self.data = None
		self.diff = None
		self.center = None
		self.corners = None

		self.length = 0
		self.distance = 0
		self.angle = 0

	def __repr__(self):
		return "Detection \"{}\" d:{:.1f}, a:{:.1f}".format(self.label, self.distance, self.angle)

	def init_from_data(self, data, detection_size, polar_transform):
		"""
		Initialize detection parameters from the pixel data from the clusterer / detection algorithm. Saved pixel data
		can also be used to (re)initialize the detection.
		"""
		self.data = data

		ca = np.cov(data, y=None, rowvar=0, bias=1)
		v, vect = np.linalg.eig(ca)
		tvect = np.transpose(vect)
		ar = np.dot(data, np.linalg.inv(tvect))

		# NOTE a fixed parameter --> to UI / file.
		if ar.shape[0] > detection_size: #10:

			mina = np.min(ar, axis=0)
			maxa = np.max(ar, axis=0)
			diff = (maxa - mina) * 0.5
					
			center = mina + diff

			# Get the 4 corners by subtracting and adding half the bounding boxes height and width to the center
			corners = np.array([center+[-diff[0],-diff[1]], \
								center+[diff[0],-diff[1]], \
								center+[diff[0],diff[1]], \
								center+[-diff[0],diff[1]], \
								center+[-diff[0],-diff[1]]])

			self.diff = diff

			# Use the the eigenvectors as a rotation matrix and rotate the corners and the center back
			self.corners = np.dot(corners, tvect)
			self.center = np.dot(center, tvect)

			if polar_transform is not None:
				metric_diff = polar_transform.pix2metCI(diff[0], diff[1])
				self.length = float(2 * metric_diff[1])
				self.distance, self.angle = polar_transform.cart2polMetric(self.center[0], self.center[1], True)
				self.distance = float(self.distance)
				self.angle = float(self.angle / np.pi * 180 + 90)

	def init_from_file(self, corners, length, distance, angle):
		"""
		Initialize detection parameters from a csv file. Data is not stored when exporting a csv file,
		which means it cannot be recovered here. This mainly affects the visualization of the detection.
		"""
		self.corners = np.array(corners)
		self.center = np.average(self.corners, axis=0)
		LogObject().print(self.center)
		self.length = length
		self.distance = distance
		self.angle = angle				

	def visualize(self, image, colors, show_text, show_detection=True):
		if self.corners is None:
			return image

		# Visualize results	
		if show_text:
			if self.length > 0:
				size_txt = 'Size: ' + str(int(100*self.length))

				image = cv2.putText(image, size_txt, (int(self.center[1])-20, int(self.center[0])-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1, cv2.LINE_AA)

		if show_detection:
			if self.data is not None:
				for i in range(self.data.shape[0]):
					cv2.line(image, (self.data[i,1], self.data[i,0]), (self.data[i,1], self.data[i,0]), \
						(int(255*colors[self.label][0]), int(255*colors[self.label][1]), int(255*colors[self.label][2])), 2)						
			for i in range(0,3):
				cv2.line(image, (int(self.corners[i,1]),int(self.corners[i,0])), (int(self.corners[i+1,1]),int(self.corners[i+1,0])),  (255,255,255), 1)
			cv2.line(image, (int(self.corners[3,1]),int(self.corners[3,0])), (int(self.corners[0,1]),int(self.corners[0,0])),  (255,255,255), 1)

		return image
	
	def getMessage(self):
		# This message was used to send data to tracker process (C++) or save detections to file
		if self.diff is None:
			return ""
		
		return str(int(self.center[1]*10)) + ' ' + str(int(self.center[0]*10)) + ' ' + str(int(self.diff[1]*2))

	def cornersToString(self, delim):
		if self.corners is None:
			return ""

		base = "{:.2f}" + delim + "{:.2f}"
		return delim.join(base.format(cx,cy) for cy, cx in self.corners[0:4])

	def convertToWritable(self):
		"""
		Returns data in applicable format to be used by SaveManager
		"""
		return [int(self.label), list(map(lambda x: [int(x[0]), int(x[1])], self.data))]


class DetectorDisplay:
	def __init__(self):
		self.array = [cv2.imread(file, 0) for file in sorted(glob.glob("out/*.png"))]
		self.detector = Detector(self)
		self.detector._show_detections = True
		self._show_echogram_detections = True

	def run(self):
		self.showWindow()
		self.detector.initMOG()

		for i in range(self.getFrameCount()):
			self.readParameters()
			images = self.detector.compute(i, self.getFrame(i), True)
			LogObject().print(images)
			self.updateWindows(*images)

	def showWindow(self):
		cv2.namedWindow('fg_mask_mog', 1)
		cv2.namedWindow('image_o_rgb', 1)
		cv2.namedWindow('image_o_gray', 1)
		cv2.namedWindow('fg_mask_filt', 1)

		cv2.createTrackbar('mog_var_thresh', 'image_o_rgb', 5, 30, nothing)
		cv2.createTrackbar('median_size', 'image_o_rgb', 1, 21, nothing)
		cv2.createTrackbar('min_fg_pixels', 'image_o_rgb', 10, 100, nothing)

		cv2.setTrackbarPos('mog_var_thresh','image_o_rgb', self.detector.bg_subtractor.mog_parameters.mog_var_thresh)
		cv2.setTrackbarPos('min_fg_pixels','image_o_rgb', self.detector.parameters.min_fg_pixels)
		cv2.setTrackbarPos('median_size','image_o_rgb', self.detector.parameters.median_size)

	def updateWindows(self, fg_mask_mog, image_o_gray, image_o_rgb, fg_mask_filt):
		pos_step = 600

		cv2.moveWindow("fg_mask_mog", pos_step*0, 20);
		cv2.moveWindow("image_o_gray", pos_step*1, 20);
		cv2.moveWindow("image_o_rgb", pos_step*2, 20);
		cv2.moveWindow("fg_mask_filt", pos_step*3, 20);

		cv2.imshow("fg_mask_mog", fg_mask_mog)
		cv2.imshow("image_o_gray", image_o_gray)
		cv2.imshow("image_o_rgb", image_o_rgb)
		cv2.imshow("fg_mask_filt", fg_mask_filt)

		sleep = cv2.getTrackbarPos('sleep','image_o_rgb')
		key = cv2.waitKey(sleep)
		if key == 32:
			sleep = -sleep

	def readParameters(self):
		# Read parameter values from trackbars
		self.detector.bg_subtractor.mog_parameters.mog_var_thresh = cv2.getTrackbarPos('mog_var_thresh','image_o_rgb')
		self.detector.parameters.min_fg_pixels = cv2.getTrackbarPos('min_fg_pixels','image_o_rgb')
		self.detector.parameters.median_size = int(round_up_to_odd(cv2.getTrackbarPos('median_size','image_o_rgb')))

	def getFrameCount(self):
		return len(self.array)

	def getFrame(self, ind):
		return self.array[ind]

def simpleTest():
	dd = DetectorDisplay()
	dd.run()

def playbackTest():

	def forwardImage(tuple):
		ind, frame = tuple
		# detections = detector.compute(ind, frame)
		detections = detector.getCurrentDetection()

		image = cv2.applyColorMap(frame, cv2.COLORMAP_OCEAN)
		image = detector.overlayDetections(image, detections)

		figure.displayImage((ind, image))

	def startDetector():
		detector.initMOG()
		playback_manager.play()

	#LogObject().disconnectDefault()
	#LogObject().connect(LogObject().defaultPrint)

	app = QtWidgets.QApplication(sys.argv)
	main_window = QtWidgets.QMainWindow()
	playback_manager = PlaybackManager(app, main_window)
	playback_manager.fps = 10
	playback_manager.openTestFile()
	playback_manager.frame_available.connect(forwardImage)
	detector = Detector(playback_manager)
	detector.bg_subtractor.mog_parameters.nof_bg_frames = 100
	detector._show_detections = True
	detector._show_echogram_detections = True
	playback_manager.mapping_done.connect(startDetector)
	playback_manager.frame_available_early.connect(detector.compute_from_event)

	figure = TestFigure(playback_manager.togglePlay)
	main_window.setCentralWidget(figure)

	main_window.show()
	sys.exit(app.exec_())


if __name__ == "__main__":
	#simpleTest()
	playbackTest()