	   label facenumber = bed + startBedPatch;

           label node0 = mesh.faces()[facenumber][0];
           label node1 = mesh.faces()[facenumber][1];
           label node2 = mesh.faces()[facenumber][2];
           label node3 = mesh.faces()[facenumber][3];

	   double x0 = newpoints[node0].x();
	   double y0 = newpoints[node0].y();
	   double z0 = newpoints[node0].z();
	   double x1 = newpoints[node1].x();
	   double y1 = newpoints[node1].y();
	   double z1 = newpoints[node1].z();
	   double x2 = newpoints[node2].x();
	   double y2 = newpoints[node2].y();
	   double z2 = newpoints[node2].z();
	   double x3 = newpoints[node3].x();
	   double y3 = newpoints[node3].y();
	   double z3 = newpoints[node3].z();

	   double ax = x2 - x0;
	   double ay = y2 - y0;
	   double az = z2 - z0;
	   double bx = x1 - x3;
	   double by = y1 - y3;
	   double bz = z1 - z3;

	   double vx = ay * bz - az * by;
	   double vy = az * bx - ax * bz;
	   double vz = ax * by - ay * bx;

	   double area = Foam::sqrt(vx*vx + vy*vy + vz*vz);

	   vx /= area;
	   vy /= area;
	   vz /= area;

//	   if(vz < 0.77) { // cos(39.64 degrees) hard-coded as angle of repose for the bed sediments
	   if(vz < cosAngleRepose) { // input angle bed sediments for slides

		outOfLoop = 0;

		double ratio = vz / cosAngleRepose; // ratio smaller than 1
		vx *= ratio;
		vy *= ratio;
		vz = Foam::sqrt(1.0 - vx*vx - vy*vy); // new larger vector component in z direction, so that the patch tilts back to angle of repose

		double xc = 0.25 * (x0 + x1 + x2 + x3);  // 25/12-24
		double yc = 0.25 * (y0 + y1 + y2 + y3);
		double zc = 0.25 * (z0 + z1 + z2 + z3);  // center of patch
		double d = - (vx * xc + vy * yc + vz * zc);  // equation for plane is  vx * x + vy * y + vz * z + d = 0
		
		double zp0 = -(vx * x0 + vy * y0 + d) / vz; // z values	for corners   
		double zp1 = -(vx * x1 + vy * y1 + d) / vz;
		double zp2 = -(vx * x2 + vy * y2 + d) / vz;
		double zp3 = -(vx * x3 + vy * y3 + d) / vz;

		newpoints[node0].z() = zp0;
		newpoints[node1].z() = zp1;
		newpoints[node2].z() = zp2;
		newpoints[node3].z() = zp3;

		}  // endif vz
		
		
		
